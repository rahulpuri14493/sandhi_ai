"""
Platform tool execution: interactive queries and artifact-first platform writes.

Reads artifact files from a mounted uploads path (Docker) or S3-compatible storage.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote, urlunparse

# Local dev: .../repo/tools/platform_mcp_server/execution_common.py -> parents[2] is repo root.
# Docker: /app/execution_common.py has only two parents (/app, /) — parents[2] raises IndexError.
_here = Path(__file__).resolve()
try:
    _repo_root = _here.parents[2]
except IndexError:
    _repo_root = None
else:
    if not (_repo_root / "backend" / "core" / "artifact_contract.py").is_file():
        _repo_root = None
if _repo_root is not None and str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
try:
    from backend.core.artifact_contract import normalize_parsed_artifact_lines
except ImportError:
    from artifact_contract import normalize_parsed_artifact_lines

logger = logging.getLogger(__name__)


def safe_tool_error(event: str, exc: BaseException) -> str:
    """
    Return a tool response safe for clients (no exception text / paths).
    Server logs record only the event label and exception type — not str(exc) or tracebacks,
    to avoid leaking connection details, SQL fragments, or filesystem paths.
    """
    logger.error("%s (%s)", event, type(exc).__name__)
    return f"Error: {event} ({type(exc).__name__})"


def _truncate_for_log(text: str, max_len: int = 2000) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) <= max_len:
        return t
    return t[:max_len] + f"... [truncated, total_chars={len(t)}]"


def _redact_object_store_key_for_log(key: str) -> str:
    """
    Log-safe representation of an object storage key: length and a short SHA-256 prefix
    for correlating log lines, without exposing any literal portion of the key itself.
    """
    s = str(key or "").strip()
    if not s:
        return ""
    n = len(s)
    digest = hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"len={n} id={digest}"


def _url_for_log(url: str) -> str:
    """Host/port/path for logs — strips userinfo (credentials) from URLs."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        u = urlparse(raw if "://" in raw else f"http://{raw}")
        if not u.hostname:
            return "<invalid-url>"
        netloc = u.hostname
        if u.port:
            netloc = f"{netloc}:{u.port}"
        return urlunparse((u.scheme or "http", netloc, u.path or "", u.params, "", u.fragment)).rstrip("/")
    except Exception:
        return "<invalid-url>"


def _postgres_dest_hint(conn_str: str) -> str:
    """Host/db for logs only — never log password."""
    try:
        u = urlparse(conn_str)
        if u.hostname:
            port = u.port or 5432
            path = unquote((u.path or "").lstrip("/"))
            db = path.split("/")[0] if path else ""
            return f"{u.hostname}:{port}/{db}" if db else f"{u.hostname}:{port}"
    except Exception:
        pass
    return "postgresql"


def _log_mcp_sql(dialect: str, query: str, *, mode: str, dest: str = "") -> None:
    """Log SQL execution metadata only — never log query text (may contain secrets or PII)."""
    n = len(query or "")
    logger.info(
        "MCP SQL dialect=%s mode=%s dest=%s query_chars=%s",
        dialect,
        mode,
        dest or "(configured)",
        n,
    )

# Docker: mount backend uploads to this prefix (see docker-compose)
_ARTIFACT_ROOT = os.environ.get("ARTIFACT_UPLOAD_ROOT", "/uploads/jobs").strip()


def _resolve_s3_compatible_endpoint(tool_type: str, config: Dict[str, Any]) -> Optional[str]:
    """
    Build boto3 endpoint_url for S3-compatible storage.

    Fixes common misconfiguration:
    - MinIO exposes the **web console** on 9001 and the **S3 API** on 9000; boto3 must use :9000.
    - Tool config often uses http://localhost:9000 from a browser on the host; inside Docker,
      localhost is the container itself, so we prefer S3_ENDPOINT_URL when it points at the real service.
    """
    raw = (config.get("endpoint") or config.get("url") or "").strip() or None
    env_ep = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None

    if tool_type in ("s3", "minio", "ceph"):
        ep = raw or env_ep
    else:
        ep = raw
    if not ep:
        return None

    # MinIO: console/UI is 9001, S3 API is 9000 — detect port via parsing (not substring on raw URL).
    if tool_type == "minio":
        ep_norm = ep if "://" in ep else f"http://{ep}"
        try:
            u_min = urlparse(ep_norm)
            if u_min.port == 9001 and u_min.hostname:
                userinfo = ""
                if u_min.username is not None:
                    userinfo = (
                        f"{u_min.username}:{u_min.password}@"
                        if u_min.password is not None
                        else f"{u_min.username}@"
                    )
                new_netloc = f"{userinfo}{u_min.hostname}:9000"
                ep = urlunparse(
                    (u_min.scheme, new_netloc, u_min.path or "", u_min.params, u_min.query, u_min.fragment)
                ).rstrip("/")
                logger.warning(
                    "MinIO endpoint used port 9001 (web console). Using port 9000 for the S3 API instead."
                )
        except Exception:
            pass

    try:
        u = urlparse(ep)
        host = (u.hostname or "").lower()
        if env_ep and host in ("localhost", "127.0.0.1", "::1"):
            e2 = urlparse(env_ep)
            h2 = (e2.hostname or "").lower()
            if h2 and h2 not in ("localhost", "127.0.0.1", "::1"):
                logger.warning(
                    "Replacing loopback S3 endpoint %s with S3_ENDPOINT_URL %s (required from inside Docker).",
                    _url_for_log(ep),
                    _url_for_log(env_ep.strip()),
                )
                return env_ep.rstrip("/")
    except Exception:
        pass

    return ep.rstrip("/")


def _artifact_object_storage_basename(path_key: str, ext: str) -> str:
    """
    Sanitize artifact path for S3/Azure/GCS keys and avoid double extensions (e.g. ...output.jsonl.jsonl).
    Backend filenames already end with .jsonl when format is jsonl.
    """
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", (path_key or "artifact")[-80:])
    ext_l = ext.lower()
    if safe.lower().endswith(ext_l):
        return safe
    for suf in (".jsonl", ".json", ".dat"):
        sl = safe.lower()
        if sl.endswith(suf):
            safe = safe[: -len(suf)]
            break
    return f"{safe}{ext}"


def is_artifact_platform_write(arguments: Dict[str, Any]) -> bool:
    """True when job executor / call-platform-write sends artifact + target."""
    if not isinstance(arguments, dict):
        return False
    ar = arguments.get("artifact_ref")
    tg = arguments.get("target")
    return (
        isinstance(ar, dict)
        and isinstance(tg, dict)
        and (arguments.get("operation_type") is not None)
        and bool(arguments.get("idempotency_key"))
    )


def _safe_ident(s: str) -> str:
    if not s or not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", s):
        raise ValueError("Invalid SQL identifier")
    return s


def _merge_sql_dialect(
    dialect: str,
    fq_table: str,
    cols: List[str],
    merge_keys: List[str],
    temp_name: str,
) -> str:
    """Build MERGE/UPSERT from staging temp table into target."""
    mk = [_safe_ident(k) for k in merge_keys]
    non_keys = [c for c in cols if c not in merge_keys]
    if dialect == "snowflake":
        on_clause = " AND ".join(f"tgt.{_safe_ident(k)} = src.{_safe_ident(k)}" for k in merge_keys)
        updates = ", ".join(f"tgt.{_safe_ident(c)} = src.{_safe_ident(c)}" for c in non_keys)
        if not updates:
            updates = f"tgt.{mk[0]} = src.{mk[0]}"
        ins_cols = ", ".join(_safe_ident(c) for c in cols)
        ins_vals = ", ".join(f"src.{_safe_ident(c)}" for c in cols)
        return (
            f"MERGE INTO {fq_table} AS tgt USING {temp_name} AS src ON {on_clause} "
            f"WHEN MATCHED THEN UPDATE SET {updates} "
            f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals});"
        )
    if dialect == "sqlserver":
        # T-SQL MERGE; bracket-quoted identifiers from _safe_ident only (same pattern as Snowflake/BQ).
        on_clause = " AND ".join(f"tgt.[{k}] = src.[{k}]" for k in mk)
        nk_safe = [_safe_ident(str(c)) for c in non_keys]
        updates = ", ".join(f"tgt.[{c}] = src.[{c}]" for c in nk_safe) or f"tgt.[{mk[0]}] = src.[{mk[0]}]"
        cols_safe = [_safe_ident(str(c)) for c in cols]
        ins_cols = ", ".join(f"[{c}]" for c in cols_safe)
        ins_vals = ", ".join(f"src.[{c}]" for c in cols_safe)
        return (
            f"MERGE INTO {fq_table} AS tgt USING {temp_name} AS src ON {on_clause} "
            f"WHEN MATCHED THEN UPDATE SET {updates} "
            f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals});"
        )
    if dialect == "bigquery":
        on_clause = " AND ".join(f"tgt.{_safe_ident(k)} = src.{_safe_ident(k)}" for k in merge_keys)
        updates = ", ".join(f"tgt.{_safe_ident(c)} = src.{_safe_ident(c)}" for c in non_keys) or f"tgt.{mk[0]} = src.{mk[0]}"
        ins_cols = ", ".join(_safe_ident(c) for c in cols)
        ins_vals = ", ".join(f"src.{_safe_ident(c)}" for c in cols)
        return (
            f"MERGE {fq_table} AS tgt USING {temp_name} AS src ON {on_clause} "
            f"WHEN MATCHED THEN UPDATE SET {updates} "
            f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals});"
        )
    raise ValueError(f"Unsupported merge dialect: {dialect}")


# SQL Server artifact MERGE: staging #temp is random hex only; fq is two bracket-quoted identifiers from _safe_ident.
_SQLSERVER_STAGING_TEMP_RE = re.compile(r"^#tmp_mcp_[0-9a-f]{32}$")
_SQLSERVER_FQ_BRACKETED_RE = re.compile(
    r"^\[[A-Za-z_][A-Za-z0-9_$]*\]\.\[[A-Za-z_][A-Za-z0-9_$]*\]$"
)
# Staging temp as it appears inside a MERGE statement (not whole-string anchored).
_SQLSERVER_STAGING_TEMP_SUB_RE = re.compile(r"#tmp_mcp_[0-9a-f]{32}")
_SQLSERVER_MERGE_INTO_FQ_RE = re.compile(
    r"MERGE\s+INTO\s+(\[[A-Za-z_][A-Za-z0-9_$]*\]\.\[[A-Za-z_][A-Za-z0-9_$]*\])\s+AS\s+tgt\s+USING",
    re.IGNORECASE,
)


def _sqlserver_validate_merge_sql(merge_sql: str) -> None:
    """
    Reject MERGE text that does not match the shape produced by _merge_sql_dialect("sqlserver", ...).
    Validates structure, disallows SQL comments, and checks temp + fq fragments against the same
    regexes used at the pymssql sink so dynamic SQL is only executed when identifiers match expectations.
    """
    if not isinstance(merge_sql, str):
        raise ValueError("Invalid MERGE SQL (not a string)")
    sql = merge_sql.strip()
    if not sql:
        raise ValueError("Invalid MERGE SQL (empty)")
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise ValueError("Invalid MERGE SQL (comment markers not allowed)")
    lowered = re.sub(r"\s+", " ", sql).strip().lower()
    required_fragments = (
        "merge into ",
        " using ",
        " on ",
        " when matched then update set ",
        " when not matched then insert ",
    )
    if not all(fragment in lowered for fragment in required_fragments):
        raise ValueError("Invalid MERGE SQL shape")
    temp_hits = _SQLSERVER_STAGING_TEMP_SUB_RE.findall(sql)
    if len(temp_hits) != 1 or not _SQLSERVER_STAGING_TEMP_RE.fullmatch(temp_hits[0]):
        raise ValueError("Invalid MERGE SQL temp table reference")
    m_fq = _SQLSERVER_MERGE_INTO_FQ_RE.search(sql)
    if not m_fq or not _SQLSERVER_FQ_BRACKETED_RE.fullmatch(m_fq.group(1)):
        raise ValueError("Invalid MERGE SQL table reference")
    # Only characters that can appear in our MERGE template (identifiers, punctuation, whitespace).
    if not re.fullmatch(r"[A-Za-z0-9_$\[\].,#=():; \t\n\r>#]+", sql):
        raise ValueError("Invalid MERGE SQL (unexpected character)")


def _sqlserver_staging_temp_name() -> str:
    """Random local #temp table name (no user-controlled characters)."""
    return f"#tmp_mcp_{secrets.token_hex(16)}"


def _pymssql_sqlserver_select_into_empty_clone(cur: Any, staging_temp: str, fq_bracketed: str) -> None:
    """Clone target schema into an empty staging #temp (identifiers validated)."""
    if not _SQLSERVER_STAGING_TEMP_RE.fullmatch(staging_temp):
        raise ValueError("Invalid SQL Server staging temp name")
    if not _SQLSERVER_FQ_BRACKETED_RE.fullmatch(fq_bracketed):
        raise ValueError("Invalid SQL Server table reference")
    cur.execute(f"SELECT * INTO {staging_temp} FROM {fq_bracketed} WHERE 1=0")


def _sqlserver_staging_insert_sql(staging_temp: str, col_sql: str, placeholders: str) -> str:
    """INSERT into staging #temp; column list/placeholders come from _safe_ident-derived fragments."""
    if not _SQLSERVER_STAGING_TEMP_RE.fullmatch(staging_temp):
        raise ValueError("Invalid SQL Server staging temp name")
    return f"INSERT INTO {staging_temp} ({col_sql}) VALUES ({placeholders})"


def _pymssql_sqlserver_execute_merge_artifact(cur: Any, merge_sql: str) -> None:
    """
    Run MERGE built by _merge_sql_dialect("sqlserver", ...).
    Dynamic SQL only concatenates _safe_ident outputs and a staging name matching _SQLSERVER_STAGING_TEMP_RE.
    _sqlserver_validate_merge_sql enforces the expected statement shape before execute (SQL injection sink).
    """
    _sqlserver_validate_merge_sql(merge_sql)
    cur.execute(merge_sql)


def _pymssql_sqlserver_drop_staging(cur: Any, staging_temp: str) -> None:
    if not _SQLSERVER_STAGING_TEMP_RE.fullmatch(staging_temp):
        raise ValueError("Invalid SQL Server staging temp name")
    cur.execute(f"DROP TABLE {staging_temp}")


def _artifact_path_segments(path: str) -> Optional[List[str]]:
    """
    Extract path segments after .../uploads/jobs/ using path splitting only (no realpath on user string).
    Rejects .. and . segment names.
    """
    p = str(path).strip().replace("\\", "/")
    segments = [x for x in p.split("/") if x]
    for i in range(len(segments) - 1):
        if segments[i] == "uploads" and segments[i + 1] == "jobs":
            rest = segments[i + 2 :]
            if not rest:
                return None
            if any(x in ("..", ".") for x in rest):
                return None
            return rest
    return None


def resolve_local_artifact_path(path: str) -> Optional[str]:
    """
    Resolve a file under ARTIFACT_UPLOAD_ROOT using only os.path.join(root, *segments).
    Never passes the raw user path to realpath(), open(), or Path().
    """
    parts = _artifact_path_segments(path)
    if not parts:
        return None
    try:
        root_real = os.path.realpath(_ARTIFACT_ROOT)
    except OSError:
        return None
    joined = os.path.join(root_real, *parts)
    try:
        real = os.path.realpath(joined)
    except OSError:
        return None
    if real != root_real and not real.startswith(root_real + os.sep):
        return None
    if not os.path.isfile(real):
        return None
    return real


def _s3_artifact_bucket_key_ok(bucket: str, key: str) -> bool:
    """
    Reject obviously unsafe S3 bucket/key values for artifact reads (path-style traversal, nulls).
    Artifact refs are produced by the trusted backend; this blocks accidental or malicious shapes.
    """
    if not bucket or not key:
        return False
    if "\x00" in bucket or "\x00" in key:
        return False
    if "/" in bucket or ".." in bucket or len(bucket) > 255:
        return False
    norm = key.replace("\\", "/")
    if ".." in norm.split("/"):
        return False
    if len(key) > 2048:
        return False
    return True


def read_artifact_bytes(artifact_ref: Dict[str, Any]) -> bytes:
    """Load artifact bytes from local path (preferred) or S3."""
    path = (artifact_ref.get("path") or "").strip()
    storage = (artifact_ref.get("storage") or "").strip().lower()
    bucket = (artifact_ref.get("bucket") or os.environ.get("S3_BUCKET") or "").strip()
    key = (artifact_ref.get("key") or path or "").strip()

    local = resolve_local_artifact_path(path) if path else None
    if local:
        return Path(local).read_bytes()

    if storage in ("s3", "minio", "ceph", "aws_s3") and bucket and key:
        if not _s3_artifact_bucket_key_ok(bucket, key):
            raise ValueError("Invalid or unsafe S3 bucket or key for artifact read")
        return _s3_get_object_bytes(bucket, key, artifact_ref)

    raise FileNotFoundError(
        f"Cannot read artifact: path={path!r} storage={storage!r}. "
        "Mount backend uploads into the platform MCP container or configure S3 credentials."
    )


def _s3_client_for_config(config: Optional[Dict[str, Any]] = None, endpoint_url: Optional[str] = None):
    import boto3

    kwargs = {}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    ak = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("S3_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("S3_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("S3_REGION") or "us-east-1"
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        kwargs["region_name"] = region
    return boto3.client("s3", **kwargs)


def _s3_get_object_bytes(bucket: str, key: str, artifact_ref: Dict[str, Any]) -> bytes:
    endpoint = os.environ.get("S3_ENDPOINT_URL") or ""
    cli = _s3_client_for_config(endpoint_url=endpoint or None)
    r = cli.get_object(Bucket=bucket, Key=key)
    return r["Body"].read()


def parse_artifact_records(data: bytes, fmt: str) -> List[Dict[str, Any]]:
    fmt = (fmt or "jsonl").lower().strip()
    if fmt == "jsonl":
        out = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
        return normalize_parsed_artifact_lines(out)
    if fmt == "json":
        j = json.loads(data.decode("utf-8"))
        if isinstance(j, list):
            out = [x for x in j if isinstance(x, dict)]
        elif isinstance(j, dict):
            out = [j]
        else:
            return []
        return normalize_parsed_artifact_lines(out)
    if fmt == "csv":
        text = data.decode("utf-8", errors="replace")
        r = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in r]
    raise ValueError(f"Unsupported artifact format: {fmt}")


def _sql_query_from_args(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """
    Return SQL text for SQL-backed tools.

    Priority:
    1) trusted tool configuration (``query`` / ``sql`` / ``statement``)
    2) runtime arguments (``query`` / ``sql`` / ``statement``) **only** when the runtime SQL
       is a strict read-only single SELECT/WITH statement.

    This keeps write SQL/DDL in trusted config or output_contract flows while allowing
    agent-generated read queries per job.

    ``arguments`` may still supply ``params`` for bound placeholders (see execution_sql).
    Invalid runtime SQL raises ``ValueError``.
    """
    if not isinstance(config, dict):
        return ""

    def _extract_query(d: Any) -> str:
        if not isinstance(d, dict):
            return ""
        # Preferred top-level keys.
        for key in ("query", "sql", "statement"):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                # Some backends store SQL under a nested value object.
                for sub in ("query", "sql", "statement", "text", "value"):
                    sv = v.get(sub)
                    if isinstance(sv, str) and sv.strip():
                        return sv.strip()
        return ""

    q = _extract_query(config)
    if q:
        return q
    # Backward-compatible nested config containers seen in tool records.
    for container_key in ("config", "settings", "options", "tool_config", "interactive_sql"):
        q = _extract_query(config.get(container_key))
        if q:
            return q

    # Fallback: runtime SQL (agent/user path) is allowed only for strict read-only queries.
    runtime_q = ""
    if isinstance(arguments, dict):
        runtime_q = str(
            arguments.get("query") or arguments.get("sql") or arguments.get("statement") or ""
        ).strip()
    if not runtime_q:
        return ""
    if not _is_safe_runtime_read_sql(runtime_q):
        raise ValueError(
            "Runtime SQL is allowed only for single read-only SELECT/WITH statements"
        )
    return runtime_q


def _is_safe_runtime_read_sql(query: str) -> bool:
    """Best-effort guard for runtime SQL: single read-only SELECT/WITH only."""
    if not isinstance(query, str):
        return False
    q = query.strip()
    if not q:
        return False
    # Disallow comments and stacked statements.
    if "--" in q or "/*" in q or "*/" in q:
        return False
    if ";" in q.rstrip(";"):
        return False
    q = q.rstrip(";").strip()
    upper = q.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False
    # Disallow common write/DDL/control keywords in runtime SQL.
    if re.search(
        r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|REPLACE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|CALL|EXEC|EXECUTE|DO|COPY)\b",
        upper,
    ):
        return False
    return True
