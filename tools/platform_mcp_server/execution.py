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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

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
        return out
    if fmt == "json":
        j = json.loads(data.decode("utf-8"))
        if isinstance(j, list):
            return [x for x in j if isinstance(x, dict)]
        if isinstance(j, dict):
            return [j]
        return []
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


def execute_postgres(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    import psycopg2

    conn_str = (config.get("connection_string") or "").strip()
    if not conn_str:
        return "Error: connection_string not configured"
    query = _sql_query_from_args(config, arguments)
    if not query:
        return "Error: query is required in arguments or tool configuration"
    params = arguments.get("params")
    upper = query.lstrip().upper()
    is_read_query = upper.startswith("SELECT") or upper.startswith("WITH")
    _log_mcp_sql(
        "postgres",
        query,
        mode="read" if is_read_query else "write",
        dest=_postgres_dest_hint(conn_str),
    )
    if is_read_query:
        try:
            conn = psycopg2.connect(conn_str)
            conn.set_session(readonly=True)
            cur = conn.cursor()
            if params is None:
                cur.execute(query)
            else:
                cur.execute(query, params)
            rows = cur.fetchall()
            colnames = [d[0] for d in cur.description] if cur.description else []
            cur.close()
            conn.close()
            if not rows:
                return "No rows returned."
            lines = ["\t".join(colnames)]
            for row in rows:
                lines.append("\t".join(str(c) for c in row))
            return "\n".join(lines)
        except Exception as e:
            logger.exception("Postgres read error")
            return f"Error: {e}"
    # write path (INSERT/UPDATE/DELETE/MERGE/DDL)
    try:
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        if params is None:
            cur.execute(query)
        else:
            cur.execute(query, params)
        conn.commit()
        rowcount = cur.rowcount
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rowcount": rowcount})
    except Exception as e:
        logger.exception("Postgres write error")
        return f"Error: {e}"


def execute_mysql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        import pymysql
    except ImportError:
        return "Error: pymysql not installed"
    query = _sql_query_from_args(config, arguments)
    if not query:
        return "Error: query is required in arguments or tool configuration"
    upper = query.lstrip().upper()
    is_read_query = upper.startswith("SELECT") or upper.startswith("WITH")
    params = arguments.get("params")
    mysql_dest = f"{config.get('host', 'localhost')}:{int(config.get('port', 3306))}/{config.get('database', '')}"
    _log_mcp_sql("mysql", query, mode="read" if is_read_query else "write", dest=mysql_dest)
    try:
        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            user=config.get("user", ""),
            password=config.get("password", ""),
            database=config.get("database", ""),
        )
        cur = conn.cursor()
        if is_read_query:
            if params is None:
                cur.execute(query)
            else:
                cur.execute(query, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            cur.close()
            conn.close()
            if not rows:
                return "No rows returned."
            return "\n".join(["\t".join(cols)] + ["\t".join(str(c) for c in row) for row in rows])
        if params is None:
            cur.execute(query)
        else:
            cur.execute(query, params)
        conn.commit()
        rc = cur.rowcount
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rowcount": rc})
    except Exception as e:
        logger.exception("MySQL error")
        return f"Error: {e}"


def execute_snowflake_sql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        import snowflake.connector
    except ImportError:
        return "Error: snowflake-connector-python is not installed"
    query = _sql_query_from_args(config, arguments)
    if not query:
        return "Error: query is required in arguments or tool configuration"
    upper = query.lstrip().upper()
    is_read_query = upper.startswith("SELECT") or upper.startswith("WITH")
    sf_dest = "/".join(
        x for x in [(config.get("database") or "").strip(), (config.get("schema") or "").strip()] if x
    )
    _log_mcp_sql(
        "snowflake",
        query,
        mode="read" if is_read_query else "write",
        dest=sf_dest or (config.get("account") or "snowflake"),
    )
    try:
        conn = snowflake.connector.connect(
            user=(config.get("user") or "").strip(),
            password=(config.get("password") or "").strip(),
            account=(config.get("account") or "").strip(),
            warehouse=(config.get("warehouse") or "").strip() or None,
            database=(config.get("database") or "").strip() or None,
            schema=(config.get("schema") or "").strip() or None,
        )
        cur = conn.cursor()
        cur.execute(query)
        if is_read_query:
            rows = cur.fetchall()
            cols = [c[0] for c in cur.description] if cur.description else []
            cur.close()
            conn.close()
            if not rows:
                return "No rows returned."
            return "\n".join(["\t".join(cols)] + ["\t".join(str(c) for c in row) for row in rows])
        cur.close()
        conn.close()
        return json.dumps({"status": "ok"})
    except Exception as e:
        logger.exception("Snowflake SQL error")
        return f"Error: {e}"


def execute_bigquery_sql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    query = _sql_query_from_args(config, arguments)
    if not query:
        return "Error: query is required in arguments or tool configuration"
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return "Error: google-cloud-bigquery is not installed"
    project = (config.get("project_id") or "").strip()
    creds_json = config.get("credentials_json")
    if creds_json:
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(project=project, credentials=creds)
    else:
        client = bigquery.Client(project=project or None)
    upper_bq = query.lstrip().upper()
    is_read_bq = upper_bq.startswith("SELECT") or upper_bq.startswith("WITH")
    _log_mcp_sql("bigquery", query, mode="read" if is_read_bq else "write", dest=project or "(default project)")
    try:
        job = client.query(query)
        if is_read_bq:
            rows = list(job.result())
            if not rows:
                return "No rows returned."
            cols = list(rows[0].keys())
            lines = ["\t".join(cols)]
            for r in rows:
                lines.append("\t".join(str(r[c]) for c in cols))
            return "\n".join(lines)
        job.result()
        return json.dumps({"status": "ok", "job_id": job.job_id})
    except Exception as e:
        logger.exception("BigQuery error")
        return f"Error: {e}"


def execute_sqlserver_sql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    query = _sql_query_from_args(config, arguments)
    if not query:
        return "Error: query is required in arguments or tool configuration"
    try:
        import pymssql
    except ImportError:
        return "Error: pymssql is not installed"
    upper_ss = query.lstrip().upper()
    is_read_ss = upper_ss.startswith("SELECT") or upper_ss.startswith("WITH")
    mssql_dest = f"{(config.get('host') or 'localhost').strip()}:{int(config.get('port') or 1433)}/{config.get('database', '')}"
    _log_mcp_sql("sqlserver", query, mode="read" if is_read_ss else "write", dest=mssql_dest)
    try:
        conn = pymssql.connect(
            server=(config.get("host") or "localhost").strip(),
            port=int(config.get("port") or 1433),
            user=(config.get("user") or "").strip(),
            password=(config.get("password") or "").strip(),
            database=(config.get("database") or "").strip(),
        )
        cur = conn.cursor()
        cur.execute(query)
        if is_read_ss:
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            cur.close()
            conn.close()
            if not rows:
                return "No rows returned."
            return "\n".join(["\t".join(str(c) for c in cols)] + ["\t".join(str(c) for c in row) for row in rows])
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "ok"})
    except Exception as e:
        logger.exception("SQL Server error")
        return f"Error: {e}"


def execute_databricks_sql(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    query = _sql_query_from_args(config, arguments)
    if not query:
        return "Error: query is required in arguments or tool configuration"
    try:
        from databricks import sql as dsql
    except ImportError:
        return "Error: databricks-sql-connector is not installed"
    host = (config.get("host") or "").strip().rstrip("/")
    token = (config.get("token") or "").strip()
    http_path = (config.get("http_path") or "").strip()
    warehouse_id = (config.get("sql_warehouse_id") or "").strip()
    if not host or not token:
        return "Error: host and token are required in tool configuration"
    # Prefer SQL warehouse HTTP path if provided; else build from warehouse id
    if not http_path and warehouse_id:
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    if not http_path:
        return "Error: sql_warehouse_id or http_path is required for Databricks"
    host_clean = host.replace("https://", "").replace("http://", "").split("/")[0].strip()
    upper_db = query.lstrip().upper()
    is_read_db = upper_db.startswith("SELECT") or upper_db.startswith("WITH")
    _log_mcp_sql("databricks", query, mode="read" if is_read_db else "write", dest=host_clean)
    try:
        conn = dsql.connect(server_hostname=host_clean, http_path=http_path, access_token=token)
        cur = conn.cursor()
        cur.execute(query)
        if is_read_db:
            rows = cur.fetchall()
            cols = [c[0] for c in cur.description] if cur.description else []
            cur.close()
            conn.close()
            if not rows:
                return "No rows returned."
            return "\n".join(["\t".join(str(c) for c in cols)] + ["\t".join(str(c) for c in row) for row in rows])
        cur.close()
        conn.close()
        return json.dumps({"status": "ok"})
    except Exception as e:
        logger.exception("Databricks error")
        return f"Error: {e}"


def execute_elasticsearch(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    import httpx

    url = (config.get("url") or config.get("host") or "").strip() or "http://localhost:9200"
    query = (arguments.get("query") or "").strip()
    if not query:
        return "Error: query is required"
    index = (arguments.get("index") or "").strip()
    size = int(arguments.get("size") or 10)
    api_key = (config.get("api_key") or "").strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"
    path = f"{url.rstrip('/')}/{index}/_search" if index else f"{url.rstrip('/')}/_search"
    body = {"query": {"query_string": {"query": query}}, "size": size}
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(path, json=body, headers=headers)
            if r.status_code == 200:
                return json.dumps(r.json(), indent=2)
            return f"Elasticsearch error: {r.status_code} {r.text}"
    except Exception as e:
        logger.exception("Elasticsearch error")
        return f"Error: {e}"


def execute_s3_family(
    tool_type: str,
    config: Dict[str, Any],
    arguments: Dict[str, Any],
) -> str:
    """S3 / MinIO / Ceph: list/get/put object."""
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        return "Error: boto3 is not installed"
    action = (arguments.get("action") or "get").strip().lower()
    key = (arguments.get("key") or "").strip()
    if not key:
        return "Error: key is required"
    bucket = (config.get("bucket") or "").strip()
    if not bucket:
        return "Error: bucket not configured"
    endpoint = _resolve_s3_compatible_endpoint(tool_type, config)
    ak = (config.get("access_key") or config.get("access_key_id") or "").strip()
    sk = (config.get("secret_key") or config.get("secret_access_key") or "").strip()
    region = (config.get("region") or os.environ.get("S3_REGION") or "us-east-1").strip()
    kwargs = {}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        kwargs["region_name"] = region
    client = boto3.client("s3", **kwargs)
    put_hint = "n/a"
    if action in ("put", "write"):
        b = arguments.get("body")
        if b is None:
            b = arguments.get("content")
        if isinstance(b, str):
            put_hint = f"{len(b)} chars"
        elif isinstance(b, dict):
            put_hint = f"dict ~{len(json.dumps(b))} chars"
        elif b is not None:
            put_hint = "binary/non-str"
    logger.info(
        "MCP s3_family tool_type=%s action=%s endpoint=%s bucket=%s key=%s payload=%s",
        tool_type,
        action,
        endpoint or "(default)",
        bucket,
        _truncate_for_log(key, 400),
        put_hint,
    )
    try:
        if action == "list":
            prefix = key.rstrip("/") + "/" if not key.endswith("/") else key
            r = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=500)
            keys = [o.get("Key") for o in r.get("Contents") or []]
            return json.dumps({"keys": keys}, indent=2)
        if action in ("get", "read"):
            obj = client.get_object(Bucket=bucket, Key=key)
            data = obj["Body"].read()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"bytes_b64": __import__("base64").b64encode(data).decode("ascii")})
        if action in ("put", "write"):
            body = arguments.get("body")
            if body is None and arguments.get("content") is not None:
                body = arguments.get("content")
            if isinstance(body, dict):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            elif body is None:
                return "Error: body or content is required for put/write"
            client.put_object(Bucket=bucket, Key=key, Body=body)
            return json.dumps({"status": "ok", "bucket": bucket, "key": key})
        return f"Error: unknown action {action}"
    except ClientError as e:
        logger.exception("S3 family error")
        return f"Error: {e}"
    except Exception as e:
        logger.exception("S3 family error")
        return f"Error: {e}"


def execute_azure_blob(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    action = (arguments.get("action") or "get").strip().lower()
    key = (arguments.get("key") or "").strip()
    if not key:
        return "Error: key is required"
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        return "Error: azure-storage-blob is not installed"
    account_url = (config.get("account_url") or "").strip()
    container = (config.get("container") or "").strip()
    conn = (config.get("connection_string") or "").strip()
    if not container:
        return "Error: container not configured"
    try:
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
        elif account_url:
            # Default credential chain when no connection string
            svc = BlobServiceClient(account_url=account_url)
        else:
            return "Error: account_url or connection_string required"
        cc = svc.get_container_client(container)
        blob = cc.get_blob_client(key)
        if action in ("get", "read"):
            data = blob.download_blob().readall()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"bytes_b64": __import__("base64").b64encode(data).decode("ascii")})
        if action in ("put", "write"):
            body = arguments.get("body") or arguments.get("content") or ""
            if isinstance(body, dict):
                body = json.dumps(body)
            blob.upload_blob(str(body).encode("utf-8") if not isinstance(body, bytes) else body, overwrite=True)
            return json.dumps({"status": "ok"})
        if action == "list":
            names = [b.name for b in cc.list_blobs(name_starts_with=key)]
            return json.dumps({"blobs": names}, indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        logger.exception("Azure blob error")
        return f"Error: {e}"


def execute_gcs(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    action = (arguments.get("action") or "get").strip().lower()
    key = (arguments.get("key") or "").strip()
    if not key:
        return "Error: key is required"
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
    except ImportError:
        return "Error: google-cloud-storage is not installed"
    project = (config.get("project_id") or "").strip()
    bucket_name = (config.get("bucket") or "").strip()
    if not bucket_name:
        return "Error: bucket not configured"
    creds_json = config.get("credentials_json")
    if creds_json:
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = storage.Client(project=project, credentials=creds)
    else:
        client = storage.Client(project=project or None)
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        if action in ("get", "read"):
            data = blob.download_as_bytes()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"bytes_b64": __import__("base64").b64encode(data).decode("ascii")})
        if action in ("put", "write"):
            body = arguments.get("body") or arguments.get("content") or ""
            if isinstance(body, dict):
                body = json.dumps(body)
            blob.upload_from_string(
                str(body) if not isinstance(body, (bytes, bytearray)) else body,
                content_type="application/json",
            )
            return json.dumps({"status": "ok"})
        if action == "list":
            names = [b.name for b in client.list_blobs(bucket_name, prefix=key)]
            return json.dumps({"objects": names}, indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        logger.exception("GCS error")
        return f"Error: {e}"


def execute_slack(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        return "Error: slack_sdk is not installed"
    token = (config.get("bot_token") or config.get("token") or "").strip()
    if not token:
        return "Error: bot_token not configured"
    action = (arguments.get("action") or "send").strip().lower()
    client = WebClient(token=token)
    try:
        if action == "list_channels":
            r = client.conversations_list(limit=200)
            ch = [c.get("name") for c in r.get("channels") or []]
            return json.dumps({"channels": ch}, indent=2)
        channel = (arguments.get("channel") or config.get("default_channel") or "").strip()
        message = (arguments.get("message") or "").strip()
        if not channel or not message:
            return "Error: channel and message are required for send"
        client.chat_postMessage(channel=channel, text=message)
        return json.dumps({"status": "ok"})
    except SlackApiError as e:
        return f"Error: {e.response['error']}"
    except Exception as e:
        logger.exception("Slack error")
        return f"Error: {e}"


def execute_github(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        from github import Github
    except ImportError:
        return "Error: PyGithub is not installed"
    token = (config.get("api_key") or config.get("token") or "").strip()
    if not token:
        return "Error: API token not configured"
    base = (config.get("base_url") or "https://api.github.com").rstrip("/")
    if base.rstrip("/").endswith("api.github.com"):
        g = Github(login_or_token=token)
    else:
        g = Github(base_url=base + "/", login_or_token=token)
    action = (arguments.get("action") or "get_file").strip().lower()
    repo_s = (arguments.get("repo") or "").strip()
    if not repo_s:
        return "Error: repo (owner/name) is required"
    repo = g.get_repo(repo_s)
    try:
        if action == "get_file":
            path = (arguments.get("path") or "").strip()
            if not path:
                return "Error: path is required"
            c = repo.get_contents(path)
            if isinstance(c, list):
                return json.dumps([{"path": x.path, "type": x.type} for x in c], indent=2)
            import base64

            data = base64.b64decode(c.content).decode("utf-8", errors="replace")
            return data
        if action == "list_issues":
            issues = repo.get_issues(state="open")
            return json.dumps([{"number": i.number, "title": i.title} for i in issues[:50]], indent=2)
        if action == "search":
            q = (arguments.get("query") or "").strip()
            r = g.search_repositories(q)
            return json.dumps([{"full_name": x.full_name} for x in r[:20]], indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        logger.exception("GitHub error")
        return f"Error: {e}"


def execute_notion(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    try:
        from notion_client import Client
    except ImportError:
        return "Error: notion-client is not installed"
    token = (config.get("api_key") or "").strip()
    if not token:
        return "Error: api_key not configured"
    client = Client(auth=token)
    action = (arguments.get("action") or "search").strip().lower()
    try:
        if action == "search":
            q = (arguments.get("query") or "").strip()
            r = client.search(query=q or "", page_size=20)
            return json.dumps(r.get("results") or [], indent=2, default=str)
        if action == "get_page":
            pid = (arguments.get("query") or "").strip()
            if not pid:
                return "Error: page id required in query"
            p = client.pages.retrieve(page_id=pid)
            return json.dumps(p, indent=2, default=str)
        if action == "get_database":
            did = (arguments.get("query") or "").strip()
            if not did:
                return "Error: database id required in query"
            d = client.databases.retrieve(database_id=did)
            return json.dumps(d, indent=2, default=str)
        return f"Error: unknown action {action}"
    except Exception as e:
        logger.exception("Notion error")
        return f"Error: {e}"


def execute_rest_api(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    import httpx

    base = (config.get("base_url") or "").strip()
    path = (arguments.get("path") or "").strip()
    method = (arguments.get("method") or "GET").upper()
    if not path:
        return "Error: path is required"
    if path.startswith("http") or "://" in path or path.startswith("/"):
        return "Error: path must be a relative path (no full URLs or leading slash)"
    if not base:
        return "Error: base_url not configured for REST API tool"
    url = base.rstrip("/") + "/" + path.lstrip("/")
    headers = {}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config.get('api_key')}"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.request(method, url, json=arguments.get("body"), headers=headers)
            ct = r.headers.get("content-type", "")
            body = r.json() if ct.startswith("application/json") else r.text
            return json.dumps({"status": r.status_code, "body": body})
    except Exception as e:
        logger.exception("REST API error")
        return f"Error: {e}"


# --- artifact-first platform writes ---


def execute_artifact_write(tool_type: str, config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    artifact_ref = arguments.get("artifact_ref") or {}
    target = arguments.get("target") or {}
    operation_type = str(arguments.get("operation_type") or "upsert").lower()
    write_mode = str(arguments.get("write_mode") or "upsert").lower()
    merge_keys: List[str] = list(arguments.get("merge_keys") or [])
    idem = str(arguments.get("idempotency_key") or "")[:200]

    try:
        raw = read_artifact_bytes(artifact_ref)
    except Exception as e:
        logger.exception("Artifact read failed")
        return f"Error: could not read artifact: {e}"

    fmt = (artifact_ref.get("format") or "jsonl").lower()
    try:
        records = parse_artifact_records(raw, fmt)
    except Exception as e:
        return f"Error: could not parse artifact ({fmt}): {e}"

    if not records:
        return "Error: artifact contained no records"

    tgt_type = (target.get("target_type") or tool_type).strip().lower()
    logger.info(
        "MCP artifact_write tool_type=%s target_type=%s operation_type=%s write_mode=%s "
        "record_count=%s merge_keys=%s artifact_storage=%s artifact_path=%s target=%s",
        tool_type,
        tgt_type,
        operation_type,
        write_mode,
        len(records),
        merge_keys,
        artifact_ref.get("storage"),
        artifact_ref.get("path") or artifact_ref.get("key"),
        _truncate_for_log(json.dumps(target, default=str), 800),
    )

    if tool_type in ("s3", "minio", "ceph", "aws_s3") or tgt_type in ("s3", "minio", "ceph", "aws_s3"):
        return _artifact_write_object_store(tool_type, config, target, records, raw, artifact_ref, operation_type, write_mode)
    if tool_type == "snowflake" or tgt_type == "snowflake":
        return _artifact_write_snowflake(config, target, records, merge_keys, operation_type, idem)
    if tool_type == "bigquery" or tgt_type == "bigquery":
        return _artifact_write_bigquery(config, target, records, merge_keys, operation_type)
    if tool_type in ("sqlserver",) or tgt_type == "sqlserver":
        return _artifact_write_sqlserver(config, target, records, merge_keys, operation_type)
    if tool_type in ("postgres",) or tgt_type == "postgres":
        return _artifact_write_postgres(config, target, records, merge_keys, operation_type, write_mode)
    if tool_type in ("mysql",) or tgt_type == "mysql":
        return _artifact_write_mysql(config, target, records, merge_keys, operation_type, write_mode)
    if tool_type in ("databricks",) or tgt_type == "databricks":
        return _artifact_write_databricks(config, target, records, merge_keys, operation_type)
    if tool_type in ("azure_blob",) or tgt_type == "azure_blob":
        return _artifact_write_azure_blob(config, target, raw, artifact_ref, operation_type)
    if tool_type in ("gcs",) or tgt_type == "gcs":
        return _artifact_write_gcs_blob(config, target, raw, artifact_ref, operation_type)

    return (
        f"Error: artifact-based platform write is not implemented for tool_type={tool_type!r} "
        f"target_type={tgt_type!r}"
    )


def _artifact_write_postgres(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
    write_mode: str,
) -> str:
    """Load JSONL/JSON artifact rows into PostgreSQL (append insert or upsert via ON CONFLICT / MERGE)."""
    try:
        import psycopg2
    except ImportError:
        return "Error: psycopg2 is not installed"
    conn_str = (config.get("connection_string") or "").strip()
    if not conn_str:
        return "Error: connection_string not configured"
    schema = (target.get("schema") or target.get("schema_name") or "public").strip()
    table = (target.get("table") or "").strip()
    if not table:
        return "Error: target.table is required for Postgres artifact write"
    cols = [str(c) for c in records[0].keys()]
    for k in merge_keys:
        if k not in cols:
            return f"Error: merge_keys must be columns of the artifact; missing {k!r}"
    fq = f'"{_safe_ident(schema)}"."{_safe_ident(table)}"'
    col_sql = ", ".join(f'"{_safe_ident(c)}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    ins = f"INSERT INTO {fq} ({col_sql}) VALUES ({placeholders})"
    tuples = [tuple(rec.get(c) for c in cols) for rec in records]
    logger.info(
        "MCP artifact Postgres write dest=%s schema=%s table=%s rows=%s operation_type=%s write_mode=%s sample_sql=%s",
        _postgres_dest_hint(conn_str),
        schema,
        table,
        len(records),
        operation_type,
        write_mode,
        _truncate_for_log(ins, 500),
    )
    try:
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        wm = str(write_mode or "").lower()
        # Full replace: empty table then append (only for plain insert, not upsert)
        if wm == "overwrite" and operation_type in ("append", "insert") and not merge_keys:
            try:
                cur.execute(f"TRUNCATE TABLE {fq} RESTART IDENTITY")
                conn.commit()
            except Exception as e:
                logger.exception("Postgres TRUNCATE for overwrite")
                cur.close()
                conn.close()
                return f"Error: overwrite (TRUNCATE) failed: {e}"
        if operation_type in ("append", "insert") or not merge_keys:
            cur.executemany(ins, tuples)
            conn.commit()
            n = cur.rowcount if hasattr(cur, "rowcount") else len(records)
            cur.close()
            conn.close()
            return json.dumps({"status": "ok", "rows": len(records), "rowcount": n})
        # Upsert / merge: multi-row INSERT ... ON CONFLICT (requires UNIQUE/PK on merge_keys)
        non_keys = [c for c in cols if c not in merge_keys]
        value_rows = []
        flat: List[Any] = []
        for rec in records:
            value_rows.append("(" + ",".join(["%s"] * len(cols)) + ")")
            flat.extend(rec.get(c) for c in cols)
        values_sql = ", ".join(value_rows)
        conflict = ", ".join(f'"{_safe_ident(k)}"' for k in merge_keys)
        if non_keys:
            update_set = ", ".join(f'"{_safe_ident(c)}" = EXCLUDED."{_safe_ident(c)}"' for c in non_keys)
            sql = (
                f"INSERT INTO {fq} ({col_sql}) VALUES {values_sql} "
                f"ON CONFLICT ({conflict}) DO UPDATE SET {update_set}"
            )
        else:
            sql = f"INSERT INTO {fq} ({col_sql}) VALUES {values_sql} ON CONFLICT ({conflict}) DO NOTHING"
        try:
            cur.execute(sql, flat)
        except Exception as e1:
            conn.rollback()
            cur.close()
            conn.close()
            return (
                f"Error: Postgres upsert requires a UNIQUE or PRIMARY KEY on ({conflict}). "
                f"Details: {e1}"
            )
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rows": len(records), "mode": "upsert"})
    except Exception as e:
        logger.exception("Postgres artifact write")
        return f"Error: {e}"


def _artifact_write_mysql(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
    write_mode: str,
) -> str:
    """Load JSONL/JSON artifact rows into MySQL (append or INSERT .. ON DUPLICATE KEY UPDATE)."""
    try:
        import pymysql
    except ImportError:
        return "Error: pymysql is not installed"
    database = (target.get("database") or config.get("database") or "").strip()
    table = (target.get("table") or "").strip()
    if not database or not table:
        return "Error: target.database and target.table are required for MySQL artifact write"
    cols = [str(c) for c in records[0].keys()]
    for k in merge_keys:
        if k not in cols:
            return f"Error: merge_keys must be columns of the artifact; missing {k!r}"
    fq = f"`{_safe_ident(database)}`.`{_safe_ident(table)}`"
    col_sql = ", ".join(f"`{_safe_ident(c)}`" for c in cols)
    tuples = [tuple(rec.get(c) for c in cols) for rec in records]
    preview_ins = f"INSERT INTO {fq} ({col_sql}) VALUES ({', '.join(['%s'] * len(cols))})"
    logger.info(
        "MCP artifact MySQL write dest=%s:%s/%s rows=%s operation_type=%s write_mode=%s sample_sql=%s",
        (config.get("host") or "localhost").strip(),
        int(config.get("port") or 3306),
        database,
        len(records),
        operation_type,
        write_mode,
        _truncate_for_log(preview_ins, 500),
    )
    try:
        conn = pymysql.connect(
            host=(config.get("host") or "localhost").strip(),
            port=int(config.get("port") or 3306),
            user=(config.get("user") or "").strip(),
            password=(config.get("password") or "").strip(),
            database=database,
        )
        cur = conn.cursor()
        wm = str(write_mode or "").lower()
        if wm == "overwrite" and operation_type in ("append", "insert") and not merge_keys:
            try:
                cur.execute(f"TRUNCATE TABLE {fq}")
                conn.commit()
            except Exception as e:
                logger.exception("MySQL TRUNCATE for overwrite")
                cur.close()
                conn.close()
                return f"Error: overwrite (TRUNCATE) failed: {e}"
        if operation_type in ("append", "insert") or not merge_keys:
            ins = f"INSERT INTO {fq} ({col_sql}) VALUES ({', '.join(['%s'] * len(cols))})"
            cur.executemany(ins, tuples)
            conn.commit()
            cur.close()
            conn.close()
            return json.dumps({"status": "ok", "rows": len(records)})
        non_keys = [c for c in cols if c not in merge_keys]
        value_rows = []
        flat: List[Any] = []
        for rec in records:
            value_rows.append("(" + ",".join(["%s"] * len(cols)) + ")")
            flat.extend(rec.get(c) for c in cols)
        values_sql = ", ".join(value_rows)
        if non_keys:
            upd = ", ".join(f"`{_safe_ident(c)}`=VALUES(`{_safe_ident(c)}`)" for c in non_keys)
            sql = f"INSERT INTO {fq} ({col_sql}) VALUES {values_sql} ON DUPLICATE KEY UPDATE {upd}"
        else:
            sql = f"INSERT INTO {fq} ({col_sql}) VALUES {values_sql} ON DUPLICATE KEY UPDATE " + ", ".join(
                f"`{_safe_ident(k)}`=`{_safe_ident(k)}`" for k in merge_keys
            )
        try:
            cur.execute(sql, flat)
            conn.commit()
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return (
                f"Error: MySQL upsert needs a UNIQUE index on the merge key columns. "
                f"Details: {e}"
            )
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rows": len(records), "mode": "upsert"})
    except Exception as e:
        logger.exception("MySQL artifact write")
        return f"Error: {e}"


def _artifact_write_object_store(
    tool_type: str,
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    raw: bytes,
    artifact_ref: Dict[str, Any],
    operation_type: str,
    write_mode: str,
) -> str:
    """Copy JSONL bytes to target bucket/prefix (append/overwrite)."""
    import boto3

    bucket = (target.get("bucket") or config.get("bucket") or "").strip()
    prefix = (target.get("prefix") or "").strip().rstrip("/")
    if not bucket:
        return "Error: target.bucket is required for object store write"
    ext = ".jsonl" if (artifact_ref.get("format") or "").lower() == "jsonl" else ".dat"
    safe_id = _artifact_object_storage_basename(str(artifact_ref.get("path") or "artifact"), ext)
    dest_key = f"{prefix}/{safe_id}" if prefix else safe_id
    endpoint = _resolve_s3_compatible_endpoint(tool_type, config)
    ak = (config.get("access_key") or config.get("access_key_id") or "").strip()
    sk = (config.get("secret_key") or config.get("secret_access_key") or "").strip()
    kwargs: Dict[str, Any] = {}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        kwargs["region_name"] = (config.get("region") or "us-east-1").strip()
    client = boto3.client("s3", **kwargs)
    try:
        body = raw if raw else ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8")
        logger.info(
            "MCP object_store_put tool_type=%s endpoint=%s bucket=%s key=%s bytes=%s operation_type=%s write_mode=%s",
            tool_type,
            endpoint or "(default)",
            bucket,
            dest_key,
            len(body),
            operation_type,
            write_mode,
        )
        if write_mode == "append" and operation_type == "append":
            try:
                existing = client.get_object(Bucket=bucket, Key=dest_key)["Body"].read()
                body = existing + body
            except Exception:
                pass
        client.put_object(Bucket=bucket, Key=dest_key, Body=body, ContentType="application/x-ndjson")
        return json.dumps({"status": "ok", "bucket": bucket, "key": dest_key, "bytes": len(body)})
    except Exception as e:
        logger.exception("Object store artifact write")
        return f"Error: {e}"


def _artifact_write_snowflake(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
    idem: str,
) -> str:
    try:
        import pandas as pd
        import snowflake.connector
        from snowflake.connector.pandas_tools import write_pandas
    except ImportError as e:
        return f"Error: snowflake / pandas tools not available: {e}"
    database = (target.get("database") or config.get("database") or "").strip()
    schema = (target.get("schema") or target.get("schema_name") or config.get("schema") or "PUBLIC").strip()
    table = (target.get("table") or "").strip()
    if not table:
        # parse name "db.schema.table"
        name = (target.get("name") or "").strip()
        parts = name.replace('"', "").split(".")
        if len(parts) == 3:
            database, schema, table = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            schema, table = parts[0], parts[1]
    if not table:
        return "Error: target.table (or fully qualified name) is required"
    try:
        conn = snowflake.connector.connect(
            user=(config.get("user") or "").strip(),
            password=(config.get("password") or "").strip(),
            account=(config.get("account") or "").strip(),
            warehouse=(config.get("warehouse") or "").strip() or None,
            database=database or None,
            schema=schema or None,
        )
        df = pd.DataFrame(records)
        fq = f"{database}.{schema}.{table}" if database else f"{schema}.{table}"
        temp = f"TMP_MCP_{re.sub(r'[^A-Za-z0-9_]', '_', idem)[:50] or 'LOAD'}"
        if operation_type in ("append", "insert") or not merge_keys:
            wp = write_pandas(conn, df, table_name=table, schema=schema, database=database)
            nrows = int(wp[2]) if isinstance(wp, tuple) and len(wp) > 2 else 0
            nchunks = int(wp[1]) if isinstance(wp, tuple) and len(wp) > 1 else 0
            ok = bool(wp[0]) if isinstance(wp, tuple) and len(wp) > 0 else True
            conn.close()
            return json.dumps({"status": "ok", "rows": nrows, "chunks": nchunks, "write_pandas": ok})
        # MERGE via staging table
        write_pandas(conn, df, table_name=temp, schema=schema, database=database, auto_create_table=True)
        cols = [str(c) for c in df.columns.tolist()]
        fq_temp = f"{database}.{schema}.{temp}" if database else f"{schema}.{temp}"
        merge_sql = _merge_sql_dialect("snowflake", fq, cols, merge_keys, fq_temp)
        cur = conn.cursor()
        cur.execute(merge_sql)
        cur.execute(f"DROP TABLE IF EXISTS {fq_temp}")
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "operation": "merge"})
    except Exception as e:
        logger.exception("Snowflake artifact write")
        return f"Error: {e}"


def _artifact_write_bigquery(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
) -> str:
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return "Error: google-cloud-bigquery is not installed"
    project = (config.get("project_id") or "").strip()
    dataset = (target.get("schema") or target.get("schema_name") or config.get("dataset") or "").strip()
    table = (target.get("table") or "").strip()
    if not dataset or not table:
        return "Error: target schema (dataset) and table are required for BigQuery"
    creds_json = config.get("credentials_json")
    if creds_json:
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(project=project, credentials=creds)
    else:
        client = bigquery.Client(project=project or None)
    table_ref = f"{project}.{dataset}.{table}" if project else f"{dataset}.{table}"
    try:
        job_config = bigquery.LoadJobConfig(
            write_disposition=(
                bigquery.WriteDisposition.WRITE_APPEND
                if operation_type in ("append", "insert")
                else bigquery.WriteDisposition.WRITE_TRUNCATE
            ),
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        )
        job = client.load_table_from_json(records, table_ref, job_config=job_config)
        job.result()
        return json.dumps({"status": "ok", "rows": len(records), "table": table_ref})
    except Exception as e:
        logger.exception("BigQuery artifact write")
        return f"Error: {e}"


def _artifact_write_sqlserver(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
) -> str:
    try:
        import pymssql
    except ImportError:
        return "Error: pymssql is not installed"
    database = (target.get("database") or config.get("database") or "").strip()
    schema = (target.get("schema") or target.get("schema_name") or "dbo").strip()
    table = (target.get("table") or "").strip()
    if not table:
        return "Error: target.table is required"
    fq = f"[{schema}].[{table}]"
    cols = list(records[0].keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(f"[{c}]" for c in cols)
    try:
        conn = pymssql.connect(
            server=(config.get("host") or "localhost").strip(),
            port=int(config.get("port") or 1433),
            user=(config.get("user") or "").strip(),
            password=(config.get("password") or "").strip(),
            database=database,
        )
        cur = conn.cursor()
        if operation_type in ("append", "insert") or not merge_keys:
            ins = f"INSERT INTO {fq} ({col_sql}) VALUES ({placeholders})"
            for rec in records:
                cur.execute(ins, tuple(rec.get(c) for c in cols))
        else:
            temp = f"#tmp_mcp_{abs(hash(fq)) % 10**8}"
            cur.execute(f"SELECT * INTO {temp} FROM {fq} WHERE 1=0")
            ins = f"INSERT INTO {temp} ({col_sql}) VALUES ({placeholders})"
            for rec in records:
                cur.execute(ins, tuple(rec.get(c) for c in cols))
            merge_sql = _merge_sql_dialect("sqlserver", fq, cols, merge_keys, temp)
            cur.execute(merge_sql)
            cur.execute(f"DROP TABLE {temp}")
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rows": len(records)})
    except Exception as e:
        logger.exception("SQL Server artifact write")
        return f"Error: {e}"


def _artifact_write_databricks(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
) -> str:
    try:
        from databricks import sql as dsql
    except ImportError:
        return "Error: databricks-sql-connector is not installed"
    host = (config.get("host") or "").strip().rstrip("/")
    token = (config.get("token") or "").strip()
    warehouse_id = (config.get("sql_warehouse_id") or "").strip()
    http_path = (config.get("http_path") or "").strip()
    if not host or not token:
        return "Error: host and token required"
    if not http_path and warehouse_id:
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    if not http_path:
        return "Error: sql_warehouse_id or http_path required"
    catalog = (target.get("database") or target.get("catalog") or "").strip()
    schema = (target.get("schema") or target.get("schema_name") or "default").strip()
    table = (target.get("table") or "").strip()
    if not table:
        return "Error: target.table is required"
    fq = f"{catalog}.{schema}.{table}" if catalog else f"{schema}.{table}"
    cols = list(records[0].keys())
    # Serialize records as JSON for inline insert via VALUES from_json
    try:
        conn = dsql.connect(
            server_hostname=host.replace("https://", "").split("/")[0],
            http_path=http_path,
            access_token=token,
        )
        cur = conn.cursor()
        # Simple: INSERT multiple VALUES — limit size
        for rec in records[:5000]:
            vals = ", ".join(_sql_literal(rec.get(c)) for c in cols)
            cur.execute(f"INSERT INTO {fq} ({', '.join(cols)}) VALUES ({vals})")
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rows": len(records)})
    except Exception as e:
        logger.exception("Databricks artifact write")
        return f"Error: {e}"


def _sql_literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _artifact_write_azure_blob(
    config: Dict[str, Any],
    target: Dict[str, Any],
    raw: bytes,
    artifact_ref: Dict[str, Any],
    operation_type: str,
) -> str:
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        return "Error: azure-storage-blob is not installed"
    container = (target.get("bucket") or config.get("container") or "").strip()
    prefix = (target.get("prefix") or "").strip().strip("/")
    if not container:
        return "Error: target.bucket/container required"
    safe_id = _artifact_object_storage_basename(str(artifact_ref.get("path") or "artifact"), ".jsonl")
    blob_name = f"{prefix}/{safe_id}" if prefix else safe_id
    conn = (config.get("connection_string") or "").strip()
    account_url = (config.get("account_url") or "").strip()
    try:
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
        else:
            svc = BlobServiceClient(account_url=account_url)
        bc = svc.get_container_client(container).get_blob_client(blob_name)
        bc.upload_blob(raw, overwrite=True)
        return json.dumps({"status": "ok", "container": container, "blob": blob_name})
    except Exception as e:
        return f"Error: {e}"


def _artifact_write_gcs_blob(
    config: Dict[str, Any],
    target: Dict[str, Any],
    raw: bytes,
    artifact_ref: Dict[str, Any],
    operation_type: str,
) -> str:
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
    except ImportError:
        return "Error: google-cloud-storage is not installed"
    bucket_name = (target.get("bucket") or config.get("bucket") or "").strip()
    prefix = (target.get("prefix") or "").strip().strip("/")
    project = (config.get("project_id") or "").strip()
    if not bucket_name:
        return "Error: target.bucket required"
    safe_id = _artifact_object_storage_basename(str(artifact_ref.get("path") or "artifact"), ".jsonl")
    blob_name = f"{prefix}/{safe_id}" if prefix else safe_id
    creds_json = config.get("credentials_json")
    if creds_json:
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = storage.Client(project=project, credentials=creds)
    else:
        client = storage.Client(project=project or None)
    try:
        b = client.bucket(bucket_name)
        b.blob(blob_name).upload_from_string(raw, content_type="application/x-ndjson")
        return json.dumps({"status": "ok", "bucket": bucket_name, "object": blob_name})
    except Exception as e:
        return f"Error: {e}"
