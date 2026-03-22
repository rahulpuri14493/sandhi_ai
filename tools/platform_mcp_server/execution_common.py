"""
Platform tool execution: interactive queries and artifact-first platform writes.

Reads artifact files from a mounted uploads path (Docker) or S3-compatible storage.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

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



def _truncate_for_log(text: str, max_len: int = 2000) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) <= max_len:
        return t
    return t[:max_len] + f"... [truncated, total_chars={len(t)}]"


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
    logger.info(
        "MCP SQL dialect=%s mode=%s dest=%s query=%s",
        dialect,
        mode,
        dest or "(configured)",
        _truncate_for_log(query),
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

    # MinIO: console/UI is 9001, S3 API is 9000 (do not rewrite arbitrary :9001 for other S3 backends)
    if tool_type == "minio" and ":9001" in ep:
        ep = ep.replace(":9001", ":9000", 1)
        logger.warning(
            "MinIO endpoint used port 9001 (web console). Using port 9000 for the S3 API instead."
        )

    try:
        u = urlparse(ep)
        host = (u.hostname or "").lower()
        if env_ep and host in ("localhost", "127.0.0.1", "::1"):
            e2 = urlparse(env_ep)
            h2 = (e2.hostname or "").lower()
            if h2 and h2 not in ("localhost", "127.0.0.1", "::1"):
                logger.warning(
                    "Replacing loopback S3 endpoint %r with S3_ENDPOINT_URL %r (required from inside Docker).",
                    ep,
                    env_ep.strip(),
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
        # T-SQL MERGE
        on_clause = " AND ".join(f"tgt.[{k}] = src.[{k}]" for k in merge_keys)
        updates = ", ".join(f"tgt.[{c}] = src.[{c}]" for c in non_keys) or f"tgt.[{merge_keys[0]}] = src.[{merge_keys[0]}]"
        ins_cols = ", ".join(f"[{c}]" for c in cols)
        ins_vals = ", ".join(f"src.[{c}]" for c in cols)
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


def resolve_local_artifact_path(path: str) -> Optional[str]:
    """Map backend-relative path to container path when uploads volume is mounted."""
    if not path or not str(path).strip():
        return None
    p = str(path).strip().replace("\\", "/")
    if p.startswith("/") and os.path.isfile(p):
        return p
    if p.startswith("uploads/jobs/"):
        tail = p[len("uploads/jobs/") :]
        candidate = os.path.join(_ARTIFACT_ROOT, tail)
        if os.path.isfile(candidate):
            return candidate
    return None


def read_artifact_bytes(artifact_ref: Dict[str, Any]) -> bytes:
    """Load artifact bytes from local path (preferred) or S3."""
    path = (artifact_ref.get("path") or "").strip()
    storage = (artifact_ref.get("storage") or "").strip().lower()
    bucket = (artifact_ref.get("bucket") or os.environ.get("S3_BUCKET") or "").strip()
    key = (artifact_ref.get("key") or path or "").strip()

    local = resolve_local_artifact_path(path) if path else None
    if local and os.path.isfile(local):
        return Path(local).read_bytes()

    if storage in ("s3", "minio", "ceph", "aws_s3") and bucket and key:
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
    if not isinstance(arguments, dict):
        return ""
    return (
        (arguments.get("query") or arguments.get("sql") or arguments.get("statement") or config.get("query") or "")
        .strip()
    )
