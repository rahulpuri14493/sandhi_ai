"""
Validate MCP tool config (connection test) before save.
Does not store any data; only checks connectivity where possible.
"""
import logging
import json
from urllib.parse import urljoin, urlparse
from typing import Tuple

from services.http_url_guard import (
    check_url_safe_for_server_fetch,
    http_hosts_allow_redirect,
    safe_url_host_for_logs,
)
from services.sql_server_host import sql_server_host_is_azure_sql

logger = logging.getLogger(__name__)


def validate_tool_config(tool_type: str, config: dict) -> Tuple[bool, str]:
    """
    Returns (valid, message). valid=True means validation succeeded.
    """
    if tool_type == "vector_db":
        return _validate_vector_db(config)
    if tool_type == "pinecone":
        return _validate_pinecone(config)
    if tool_type == "weaviate":
        return _validate_weaviate(config)
    if tool_type == "qdrant":
        return _validate_qdrant(config)
    if tool_type == "chroma":
        return _validate_chroma(config)
    if tool_type == "postgres":
        return _validate_postgres(config)
    if tool_type == "mysql":
        return _validate_mysql(config)
    if tool_type == "sqlserver":
        return _validate_sqlserver(config)
    if tool_type == "snowflake":
        return _validate_snowflake(config)
    if tool_type == "databricks":
        return _validate_databricks(config)
    if tool_type == "bigquery":
        return _validate_bigquery(config)
    if tool_type == "filesystem":
        return _validate_filesystem(config)
    if tool_type in ("elasticsearch", "rest_api"):
        return _validate_http(config, tool_type)
    if tool_type == "pageindex":
        return _validate_pageindex(config)
    if tool_type in ("s3", "minio", "ceph"):
        return _validate_s3_family(config, tool_type)
    if tool_type == "azure_blob":
        return _validate_azure_blob(config)
    if tool_type == "gcs":
        return _validate_gcs(config)
    if tool_type == "slack":
        return _validate_slack(config)
    if tool_type == "teams":
        return _validate_teams(config)
    if tool_type == "smtp":
        return _validate_smtp(config)
    if tool_type == "github":
        return _validate_github(config)
    if tool_type == "notion":
        return _validate_notion(config)
    return False, f"Unsupported tool type for validation: {tool_type}"


def _normalize_http_url(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    # Users (or agents) sometimes paste URLs with trailing punctuation from markdown
    # or prose. Strip a small, safe set before normalization so that host parsing works.
    s = s.rstrip(").],>")
    if not s:
        return ""
    if not s.startswith("http://") and not s.startswith("https://"):
        s = "https://" + s
    return s


def _http_url_has_host(url: str) -> bool:
    try:
        return bool(urlparse(url).hostname)
    except Exception:
        return False


_HTTP_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_MAX_VALIDATE_HTTP_REDIRECTS = 10


def _http_url_hostname_lower(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _http_reachable(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 6.0,
    restrict_same_host_redirects: bool = False,
) -> Tuple[bool, str]:
    import httpx

    if not restrict_same_host_redirects:
        try:
            r = httpx.get(url, headers=headers or {}, timeout=timeout)
            if r.status_code < 500:
                return True, f"Endpoint reachable (HTTP {r.status_code})"
            return False, f"Endpoint returned HTTP {r.status_code}"
        except Exception:
            logger.exception("HTTP reachability check failed host=%s", safe_url_host_for_logs(url))
            return False, "Unable to reach endpoint; verify URL, network, and credentials."

    anchor = _http_url_hostname_lower(url)
    if not anchor:
        return False, "URL is invalid"
    current = url
    n_redirects = 0
    try:
        while True:
            r = httpx.get(current, headers=headers or {}, timeout=timeout, follow_redirects=False)
            if r.status_code not in _HTTP_REDIRECT_STATUSES:
                if r.status_code < 500:
                    return True, f"Endpoint reachable (HTTP {r.status_code})"
                return False, f"Endpoint returned HTTP {r.status_code}"
            if n_redirects >= _MAX_VALIDATE_HTTP_REDIRECTS:
                return False, "Too many HTTP redirects while validating (limit exceeded)."
            n_redirects += 1
            loc = r.headers.get("location")
            if not loc or not str(loc).strip():
                return (
                    False,
                    f"Endpoint returned HTTP {r.status_code} without a Location header (redirect cannot be validated).",
                )
            next_url = urljoin(current, str(loc).strip())
            safe, reason = check_url_safe_for_server_fetch(next_url, purpose="validate_http_redirect")
            if not safe:
                return False, f"Redirect blocked (SSRF policy): {reason}"
            if not http_hosts_allow_redirect(anchor, _http_url_hostname_lower(next_url)):
                return (
                    False,
                    "Redirect blocked: target host is not the same hostname or registrable domain "
                    "as the configured URL (e.g. api.example.com → cdn.example.com is allowed; "
                    "another.example.org is not).",
                )
            current = next_url
    except Exception:
        logger.exception("HTTP reachability check failed host=%s", safe_url_host_for_logs(url))
        return False, "Unable to reach endpoint; verify URL, network, and credentials."


def _validate_vector_db(config: dict) -> Tuple[bool, str]:
    url = _normalize_http_url(config.get("url") or config.get("host") or "")
    if not url:
        return False, "Vector DB URL is required"
    if not _http_url_has_host(url):
        return False, "Vector DB URL is invalid"
    return _http_reachable(url)


def _validate_pinecone(config: dict) -> Tuple[bool, str]:
    api_key = (config.get("api_key") or "").strip()
    host = _normalize_http_url(config.get("host") or config.get("url") or "")
    if not api_key:
        return False, "Pinecone API key is required"
    if not host:
        return False, "Pinecone host is required"
    try:
        from pinecone import Pinecone
    except ImportError:
        return False, "Pinecone validation requires the pinecone client (not installed in backend)"
    try:
        pc = Pinecone(api_key=api_key)
        idx = pc.Index(host=host.replace("https://", "").replace("http://", "").split("/")[0].strip())
        idx.describe_index_stats()
        return True, "Pinecone connection successful"
    except Exception:
        logger.exception("Pinecone validation failed")
        return False, "Unable to connect to Pinecone; please verify host, API key, and network connectivity."


def _validate_weaviate(config: dict) -> Tuple[bool, str]:
    url = _normalize_http_url(config.get("url") or "")
    if not url:
        return False, "Weaviate URL is required"
    if not _http_url_has_host(url):
        return False, "Weaviate URL is invalid"
    api_key = (config.get("api_key") or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # /v1/meta is a lightweight endpoint available on most Weaviate deployments.
    return _http_reachable(url.rstrip("/") + "/v1/meta", headers=headers)


def _validate_qdrant(config: dict) -> Tuple[bool, str]:
    url = _normalize_http_url(config.get("url") or "")
    if not url:
        return False, "Qdrant URL is required"
    if not _http_url_has_host(url):
        return False, "Qdrant URL is invalid"
    api_key = (config.get("api_key") or "").strip()
    headers = {"api-key": api_key} if api_key else {}
    return _http_reachable(url.rstrip("/") + "/collections", headers=headers)


def _validate_chroma(config: dict) -> Tuple[bool, str]:
    url = _normalize_http_url(config.get("url") or "")
    if not url:
        return False, "Chroma URL is required"
    if not _http_url_has_host(url):
        return False, "Chroma URL is invalid"
    api_key = (config.get("api_key") or config.get("chroma_api_key") or config.get("chroma_token") or "").strip()
    headers = {"X-Chroma-Token": api_key} if api_key else {}
    ok, msg = _http_reachable(url.rstrip("/") + "/api/v1/heartbeat", headers=headers)
    if ok:
        return ok, "Chroma connection successful"
    # Some deployments expose v2 heartbeat only.
    ok2, _ = _http_reachable(url.rstrip("/") + "/api/v2/heartbeat", headers=headers)
    if ok2:
        return True, "Chroma connection successful"
    # Save/validate UX: a well-formed URL is enough; CI and laptops often have no Chroma listening.
    return (
        True,
        "Chroma URL is valid; live heartbeat not verified (server offline or unreachable). "
        f"{msg}",
    )


def _validate_postgres(config: dict) -> Tuple[bool, str]:
    try:
        import psycopg2
        conn_str = (config.get("connection_string") or "").strip()
        if not conn_str:
            return False, "Connection string is required"
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return True, "PostgreSQL connection successful"
    except Exception as e:
        logger.exception("PostgreSQL validation failed")
        err_text = str(e)
        base_msg = "Unable to connect to PostgreSQL database; please check host, port, and credentials."
        if "connection refused" in err_text.lower() and ("localhost" in conn_str or "127.0.0.1" in conn_str):
            base_msg += " When the app runs in Docker, use host.docker.internal instead of localhost to reach PostgreSQL on your host (e.g. postgresql://postgres:postgres@host.docker.internal:5432/agent_marketplace). To use the Docker Compose Postgres service, use host 'db' (e.g. postgresql://postgres:postgres@db:5432/agent_marketplace)."
        return False, base_msg


def _validate_mysql(config: dict) -> Tuple[bool, str]:
    try:
        import pymysql
        kwargs = _mysql_connect_kwargs(config)
        kwargs["connect_timeout"] = 5
        conn = pymysql.connect(**kwargs)
        conn.ping()
        conn.close()
        return True, "MySQL connection successful"
    except ImportError:
        return False, "MySQL validation requires pymysql (not installed in backend)"
    except Exception as e:
        logger.exception("MySQL validation failed")
        return False, _mysql_validation_error_message(e)


def _mysql_connect_kwargs(config: dict) -> dict:
    kwargs = {
        "host": config.get("host", "localhost"),
        "port": int(config.get("port", 3306)),
        "user": config.get("user", ""),
        "password": config.get("password", ""),
        "database": config.get("database", ""),
    }
    mode = str(config.get("ssl_mode") or "").strip().lower()

    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        return str(v or "").strip().lower() in ("1", "true", "yes", "on", "required", "require")

    if mode == "require":
        mode = "required"
    want_tls = mode in ("preferred", "required", "verify_ca", "verify_identity")
    want_tls = want_tls or _truthy(config.get("ssl")) or _truthy(config.get("tls")) or _truthy(config.get("require_secure_transport"))
    if mode == "disabled":
        want_tls = False
    if want_tls:
        ssl_opts = {}
        for src, dst in (("ssl_ca", "ca"), ("ssl_cert", "cert"), ("ssl_key", "key")):
            val = str(config.get(src) or "").strip()
            if val:
                ssl_opts[dst] = val
        # PyMySQL enables TLS only when `ssl` is truthy; avoid empty dict for mode=required.
        # Keep defaults permissive unless caller explicitly requests verification.
        if str(config.get("ssl_check_hostname") or "").strip():
            ssl_opts["check_hostname"] = _truthy(config.get("ssl_check_hostname"))
        elif mode == "verify_identity":
            ssl_opts["check_hostname"] = True
        else:
            ssl_opts["check_hostname"] = False
        if mode in ("verify_ca", "verify_identity"):
            ssl_opts["verify_mode"] = "required"
        else:
            ssl_opts.setdefault("verify_mode", "none")
        kwargs["ssl"] = ssl_opts
    return kwargs


def _mysql_validation_error_message(exc: Exception) -> str:
    low = str(exc).lower()
    if "require_secure_transport" in low or "insecure transport" in low:
        return (
            "MySQL requires secure transport (TLS). Set ssl_mode='required' (or ssl=true) in tool config, "
            "and provide ssl_ca if your provider requires CA verification."
        )
    return "Unable to connect to MySQL database; please check host, port, and credentials."


def _validate_sqlserver(config: dict) -> Tuple[bool, str]:
    host = (config.get("host") or "localhost").strip()
    user = (config.get("user") or "").strip()
    password = (config.get("password") or "").strip()
    database = (config.get("database") or "").strip()
    if not host:
        return False, "SQL Server host is required"
    if not user:
        return False, "SQL Server user is required"
    if not password:
        return False, "SQL Server password is required"
    if not database:
        return False, "SQL Server database is required"

    try:
        import pymssql
    except ImportError:
        return False, "SQL Server validation requires pymssql (not installed in backend)"

    kw = {
        "server": host,
        "port": int(config.get("port") or 1433),
        "user": user,
        "password": password,
        "database": database,
    }
    encryption = (config.get("encryption") or config.get("encrypt") or "").strip().lower()
    if encryption in ("off", "request", "require"):
        kw["encryption"] = encryption
    elif sql_server_host_is_azure_sql(host):
        # Azure SQL Database requires TLS; "require" is the safest default.
        kw["encryption"] = "require"
        kw["login_timeout"] = 90
    try:
        conn = pymssql.connect(**kw)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return True, "SQL Server connection successful"
    except Exception as e:
        logger.exception("SQL Server validation failed")
        return False, _sqlserver_validation_error_message(e, host, user)


def _sqlserver_validation_error_message(exc: Exception, host: str, user: str) -> str:
    low = str(exc).lower()
    base = "Unable to connect to SQL Server; please check host, port, database, and credentials."
    is_azure = sql_server_host_is_azure_sql(host)
    user_at_count = (user or "").count("@")

    if "login failed for user" in low or "18456" in low:
        msg = (
            "SQL Server authentication failed (invalid username/password for this database). "
            "Use a SQL login that exists on this SQL Server."
        )
        if is_azure:
            msg += (
                " For Azure SQL, common login format is user@logical-server "
                "(example: admin@sandhiai)."
            )
            if user_at_count > 1:
                msg += " Your username appears to contain multiple '@' symbols; check the login format."
        return msg

    if "certificate" in low or "ssl" in low or "tls" in low or "handshake" in low:
        return (
            "TLS/SSL handshake failed while connecting to SQL Server. "
            "Enable encryption for Azure SQL (encryption=require) and verify server certificate settings."
        )

    if "connection refused" in low or "adaptive server is unavailable" in low:
        msg = (
            "SQL Server host/port is unreachable from the backend container. "
            "Verify host, port 1433, and network/firewall access."
        )
        if is_azure:
            msg += " In Azure SQL, add the backend egress public IP to SQL Server firewall rules."
        return msg

    if "timeout" in low or "timed out" in low:
        msg = "SQL Server connection timed out. Verify network reachability and firewall rules."
        if is_azure:
            msg += " If database is serverless, ensure it is Online (not Paused) and retry after resume."
        return msg

    if is_azure:
        base += (
            " Azure SQL checklist: database Online (not Paused), firewall allows backend egress IP, "
            "SQL login format user@logical-server (example admin@sandhiai), encryption=require."
        )
        if user_at_count > 1:
            base += " Username contains multiple '@' symbols; verify login format."
    return base


def _validate_snowflake(config: dict) -> Tuple[bool, str]:
    user = (config.get("user") or "").strip()
    password = (config.get("password") or "").strip()
    account = (config.get("account") or "").strip()
    if not user:
        return False, "Snowflake user is required"
    if not password:
        return False, "Snowflake password is required"
    if not account:
        return False, "Snowflake account is required"
    try:
        import snowflake.connector
    except ImportError:
        return False, "Snowflake validation requires snowflake-connector-python (not installed in backend)"
    try:
        conn = snowflake.connector.connect(
            user=user,
            password=password,
            account=account,
            role=(config.get("role") or "").strip() or None,
            warehouse=(config.get("warehouse") or "").strip() or None,
            database=(config.get("database") or "").strip() or None,
            schema=(config.get("schema") or "").strip() or None,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return True, "Snowflake connection successful"
    except Exception:
        logger.exception("Snowflake validation failed")
        return False, "Unable to connect to Snowflake; please check account, user, and credentials."


def _validate_databricks(config: dict) -> Tuple[bool, str]:
    host = (config.get("host") or "").strip()
    token = (config.get("token") or "").strip()
    http_path = (config.get("http_path") or "").strip()
    warehouse_id = (config.get("sql_warehouse_id") or "").strip()
    if not host:
        return False, "Databricks host is required"
    if not token:
        return False, "Databricks token is required"
    if not http_path and not warehouse_id:
        return False, "Databricks http_path or sql_warehouse_id is required"
    try:
        from databricks import sql as dsql
    except ImportError:
        return False, "Databricks validation requires databricks-sql-connector (not installed in backend)"
    if not http_path and warehouse_id:
        wh = str(warehouse_id).strip()
        # Users sometimes paste full HTTP paths (e.g. /sql/1.0/warehouses/<id>) into "SQL warehouse ID".
        # Detect that and avoid double-prefixing.
        if wh.startswith("/sql/") or "/sql/1.0/warehouses/" in wh:
            http_path = wh if wh.startswith("/") else f"/{wh}"
        else:
            http_path = f"/sql/1.0/warehouses/{wh}"
    host_clean = host.replace("https://", "").replace("http://", "").split("/")[0].strip()
    try:
        conn = dsql.connect(server_hostname=host_clean, http_path=http_path, access_token=token)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        cur.close()
        conn.close()
        return True, "Databricks SQL connection successful"
    except Exception:
        logger.exception("Databricks validation failed")
        msg = "Unable to connect to Databricks SQL warehouse; check host, token, and warehouse path."
        if str(config.get("debug") or "").strip().lower() in ("1", "true", "yes", "on") or (
            str(__import__("os").environ.get("MCP_DATABRICKS_INCLUDE_ERROR_DETAIL", "")).strip().lower()
            in ("1", "true", "yes", "on")
        ):
            # Best-effort: include exception type + message (truncated). Should not contain the PAT.
            import sys

            exc = sys.exc_info()[1]
            if exc is not None:
                detail = (str(exc) or "").strip().replace("\n", " ")
                msg += f" Details: {type(exc).__name__}: {detail[:300]}"
        return False, msg


def _validate_bigquery(config: dict) -> Tuple[bool, str]:
    project = (config.get("project_id") or "").strip()
    if not project:
        return False, "BigQuery project_id is required"
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return False, "BigQuery validation requires google-cloud-bigquery (not installed in backend)"
    try:
        creds_json = config.get("credentials_json")
        if creds_json:
            info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
            creds = service_account.Credentials.from_service_account_info(info)
            client = bigquery.Client(project=project, credentials=creds)
        else:
            client = bigquery.Client(project=project)
        job = client.query("SELECT 1")
        list(job.result())
        return True, "BigQuery connection successful"
    except Exception:
        logger.exception("BigQuery validation failed")
        return False, "Unable to connect to BigQuery; verify project_id, credentials, and IAM permissions."


def _validate_filesystem(config: dict) -> Tuple[bool, str]:
    import os
    base = (config.get("base_path") or "").strip()
    if not base:
        return False, "Base path is required"
    if not os.path.isdir(base):
        return False, f"Base path is not a directory or does not exist: {base}"
    return True, "Base path exists and is readable"


def _validate_pageindex(config: dict) -> Tuple[bool, str]:
    """Validate PageIndex: require api_key and optionally hit list-docs API."""
    import httpx
    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        return False, "API key is required (get one at https://dash.pageindex.ai)"
    base = (config.get("base_url") or "https://api.pageindex.ai").strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    try:
        r = httpx.get(f"{base}/docs", headers={"api_key": api_key}, params={"limit": 1}, timeout=10.0)
        if r.status_code == 401:
            return False, "Invalid PageIndex API key"
        if r.status_code >= 400:
            return False, f"PageIndex API returned {r.status_code}: {r.text[:200]}"
        return True, "PageIndex connection successful"
    except Exception:
        logger.exception("PageIndex validation failed")
        return False, "Unable to reach PageIndex API; please verify the base URL and network connectivity."


def _validate_http(config: dict, tool_type: str) -> Tuple[bool, str]:
    url = _normalize_http_url(config.get("url") or config.get("base_url") or "")
    if not url:
        return False, "URL is required"
    if not _http_url_has_host(url):
        return False, "URL is invalid"
    safe, reason = check_url_safe_for_server_fetch(url, purpose=f"validate_{tool_type}")
    if not safe:
        return False, reason
    target = url.rstrip("/") + "/" if tool_type == "elasticsearch" else url
    return _http_reachable(target, restrict_same_host_redirects=True)


def _validate_s3_family(config: dict, tool_type: str) -> Tuple[bool, str]:
    bucket = (config.get("bucket") or "").strip()
    if not bucket:
        return False, f"{tool_type} bucket is required"
    try:
        import boto3
    except ImportError:
        return False, "S3 validation requires boto3 (not installed in backend)"
    endpoint = (config.get("endpoint") or config.get("url") or "").strip()
    ak = (config.get("access_key") or config.get("access_key_id") or "").strip()
    sk = (config.get("secret_key") or config.get("secret_access_key") or "").strip()
    region = (config.get("region") or "us-east-1").strip()
    kwargs = {}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        kwargs["region_name"] = region
    try:
        client = boto3.client("s3", **kwargs)
        client.list_objects_v2(Bucket=bucket, MaxKeys=1)
        return True, f"{tool_type} connection successful"
    except Exception:
        logger.exception("%s validation failed", tool_type)
        return False, f"Unable to access {tool_type} bucket; verify endpoint, bucket, credentials, and network."


def _validate_azure_blob(config: dict) -> Tuple[bool, str]:
    container = (config.get("container") or "").strip()
    account_url = (config.get("account_url") or "").strip()
    conn = (config.get("connection_string") or "").strip()
    if not container:
        return False, "Azure Blob container is required"
    if not account_url and not conn:
        return False, "Azure Blob account_url or connection_string is required"
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        return False, "Azure Blob validation requires azure-storage-blob (not installed in backend)"
    try:
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
        else:
            try:
                from azure.identity import DefaultAzureCredential

                svc = BlobServiceClient(account_url, credential=DefaultAzureCredential())
            except ImportError:
                svc = BlobServiceClient(account_url=account_url)
        cc = svc.get_container_client(container)
        cc.exists()
        return True, "Azure Blob connection successful"
    except Exception:
        logger.exception("Azure Blob validation failed")
        return False, "Unable to access Azure Blob container; verify account URL/connection string, container, and credentials."


def _validate_gcs(config: dict) -> Tuple[bool, str]:
    bucket_name = (config.get("bucket") or "").strip()
    if not bucket_name:
        return False, "GCS bucket is required"
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
    except ImportError:
        return False, "GCS validation requires google-cloud-storage (not installed in backend)"
    try:
        project = (config.get("project_id") or "").strip()
        creds_json = config.get("credentials_json")
        if creds_json:
            info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
            creds = service_account.Credentials.from_service_account_info(info)
            client = storage.Client(project=project or None, credentials=creds)
        else:
            client = storage.Client(project=project or None)
        exists = client.bucket(bucket_name).exists()
        if exists:
            return True, "GCS connection successful"
        return False, "GCS bucket not found or not accessible"
    except Exception:
        logger.exception("GCS validation failed")
        return False, "Unable to access GCS bucket; verify project, bucket, credentials, and IAM permissions."


def _gmail_read_api_probe(access_token: str) -> str:
    """Non-blocking check after Gmail SMTP OAuth succeeds; confirms inbox read scopes work."""
    try:
        import httpx
    except ImportError:
        return ""
    try:
        r = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        if r.status_code == 200:
            return " Gmail REST API (read) OK."
        return (
            f" Warning: Gmail REST returned HTTP {r.status_code}; "
            "list_mail_messages may fail until you reconnect Google with gmail.readonly."
        )
    except Exception:
        return " Warning: could not reach Gmail REST API; check network if you need inbox read."


def _smtp_validate_oauth_provider_for_refresh(config: dict) -> str:
    prov = str(config.get("provider") or "custom").strip().lower()
    if prov in ("outlook", "gmail"):
        return prov
    host = str(config.get("smtp_host") or "").strip().lower()
    if "office365.com" in host or host == "smtp-mail.outlook.com":
        return "outlook"
    if "gmail.com" in host:
        return "gmail"
    return ""


def _smtp_validate_refresh_access_token(config: dict) -> bool:
    """
    Refresh OAuth access_token using stored refresh token and MCP_OAUTH_* settings.
    Mutates config in place. Used so validation matches platform MCP after access tokens expire.
    """
    refresh = str(config.get("oauth_refresh_token") or config.get("refresh_token") or "").strip()
    if not refresh:
        return False
    prov = _smtp_validate_oauth_provider_for_refresh(config)
    try:
        from core.config import settings
        import httpx
    except Exception:
        return False
    try:
        if prov == "outlook":
            cid = (getattr(settings, "MCP_OAUTH_MICROSOFT_CLIENT_ID", "") or "").strip()
            secret = (getattr(settings, "MCP_OAUTH_MICROSOFT_CLIENT_SECRET", "") or "").strip()
            tenant = (getattr(settings, "MCP_OAUTH_MICROSOFT_TENANT", "") or "common").strip() or "common"
            if not cid or not secret:
                return False
            token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            data = {
                "client_id": cid,
                "client_secret": secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            }
            r = httpx.post(token_url, data=data, timeout=30.0)
        elif prov == "gmail":
            cid = (getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_ID", "") or "").strip()
            secret = (getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_SECRET", "") or "").strip()
            if not cid or not secret:
                return False
            r = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": cid,
                    "client_secret": secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
                timeout=30.0,
            )
        else:
            return False
    except Exception:
        logger.exception("SMTP OAuth token refresh failed during validation")
        return False
    if r.status_code != 200:
        return False
    try:
        body = r.json()
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    at = str(body.get("access_token") or "").strip()
    if not at:
        return False
    config["access_token"] = at
    new_rt = str(body.get("refresh_token") or "").strip()
    if new_rt:
        config["oauth_refresh_token"] = new_rt
    return True


def _validate_teams(config: dict) -> Tuple[bool, str]:
    token = (config.get("access_token") or config.get("oauth2_access_token") or "").strip()
    if not token:
        return False, "Microsoft Graph access_token is required for Teams"
    try:
        import httpx
    except ImportError:
        return False, "Teams validation requires httpx (not installed in backend)"
    try:
        r = httpx.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if r.status_code == 200:
            return True, "Microsoft Graph / Teams token validated"
        return False, f"Graph API returned HTTP {r.status_code}; verify token and Teams permissions."
    except Exception:
        logger.exception("Teams / Graph validation failed")
        return False, "Unable to reach Microsoft Graph; verify token, scopes, and network."


def _validate_smtp(config: dict) -> Tuple[bool, str]:
    import smtplib
    import ssl

    provider = str(config.get("provider") or "custom").strip().lower()
    presets = {
        "gmail": ("smtp.gmail.com", 587, False),
        "outlook": ("smtp.office365.com", 587, False),
        "yahoo": ("smtp.mail.yahoo.com", 587, False),
    }
    if provider in presets:
        host, port, use_ssl = presets[provider]
        use_starttls = not use_ssl
    else:
        host = (config.get("smtp_host") or "").strip()
        try:
            port = int(config.get("smtp_port") or 587)
        except (TypeError, ValueError):
            port = 587
        use_ssl = bool(config.get("use_ssl"))
        use_starttls = bool(config.get("use_tls", True))
    if not host:
        return False, "SMTP host is required (or choose provider gmail/outlook/yahoo)"
    auth_mode = str(config.get("auth_mode") or "").strip().lower()
    username = str(config.get("username") or config.get("from_address") or "").strip()
    password = str(config.get("password") or "").strip()
    access_token = str(config.get("access_token") or config.get("oauth2_access_token") or "").strip()
    refresh_tok = str(config.get("oauth_refresh_token") or config.get("refresh_token") or "").strip()
    if not auth_mode:
        auth_mode = "oauth2" if (access_token or refresh_tok) else "password"
    ctx = ssl.create_default_context()
    timeout = 25.0
    try:
        if use_ssl or port == 465:
            client = smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout)
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
            client.ehlo()
            if use_starttls:
                client.starttls(context=ctx)
                client.ehlo()
        try:
            if auth_mode == "oauth2":
                if not username:
                    return False, "username (mailbox) is required for OAuth2 SMTP"
                if not access_token and refresh_tok:
                    _smtp_validate_refresh_access_token(config)
                    access_token = str(config.get("access_token") or config.get("oauth2_access_token") or "").strip()
                if not access_token:
                    return False, (
                        "username and access_token are required for OAuth2 SMTP, or oauth_refresh_token plus "
                        "MCP_OAUTH_MICROSOFT_* / MCP_OAUTH_GOOGLE_* in backend settings to refresh expired tokens."
                    )
                import base64

                auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
                b64 = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
                code, resp = client.docmd("AUTH", "XOAUTH2 " + b64)
                if code != 235 and refresh_tok:
                    if _smtp_validate_refresh_access_token(config):
                        access_token = str(config.get("access_token") or config.get("oauth2_access_token") or "").strip()
                        auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
                        b64 = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
                        code, resp = client.docmd("AUTH", "XOAUTH2 " + b64)
                if code != 235:
                    detail = resp.decode(errors="replace") if isinstance(resp, bytes) else str(resp)
                    msg = f"SMTP OAuth2 rejected ({code}): {detail.strip()[:400]}"
                    if code == 535:
                        msg += (
                            " Hint: use OAuth scope https://outlook.office.com/SMTP.Send (not Graph-only) for "
                            "smtp.office365.com; enable Authenticated SMTP on the mailbox. "
                            "If this worked earlier, the access token may have expired — save oauth_refresh_token and "
                            "ensure backend has the same OAuth app credentials used for Connect Microsoft."
                        )
                    return False, msg
            else:
                if not username or not password:
                    return False, "username and password are required for SMTP password auth"
                client.login(username, password)
            msg_ok = "SMTP connection and authentication successful"
            if auth_mode == "oauth2" and provider == "gmail" and access_token:
                msg_ok += _gmail_read_api_probe(access_token)
            return True, msg_ok
        finally:
            try:
                client.quit()
            except Exception:
                pass
    except Exception:
        logger.exception("SMTP validation failed")
        return False, "SMTP validation failed; verify host, port, credentials, and TLS settings."


def _validate_slack(config: dict) -> Tuple[bool, str]:
    token = (config.get("bot_token") or config.get("token") or "").strip()
    if not token:
        return False, "Slack bot token is required"
    try:
        from slack_sdk import WebClient
    except ImportError:
        return False, "Slack validation requires slack_sdk (not installed in backend)"
    try:
        WebClient(token=token).auth_test()
        return True, "Slack connection successful"
    except Exception:
        logger.exception("Slack validation failed")
        return False, "Unable to authenticate with Slack; verify the bot token."


def _validate_github(config: dict) -> Tuple[bool, str]:
    token = (config.get("api_key") or config.get("token") or "").strip()
    if not token:
        return False, "GitHub token is required"
    base = (config.get("base_url") or "https://api.github.com").rstrip("/")
    try:
        from github import Github
    except ImportError:
        return False, "GitHub validation requires PyGithub (not installed in backend)"
    try:
        g = Github(login_or_token=token) if _github_host_is_api_github_com(base) else Github(base_url=base + "/", login_or_token=token)
        _ = g.get_user().login
        return True, "GitHub connection successful"
    except Exception:
        logger.exception("GitHub validation failed")
        return False, "Unable to authenticate with GitHub; verify token permissions and base URL."


def _github_host_is_api_github_com(base: str) -> bool:
    s = (base or "").strip()
    if not s:
        return True
    if "://" not in s:
        s = "https://" + s
    try:
        return (urlparse(s).hostname or "").lower() == "api.github.com"
    except Exception:
        return False


def _validate_notion(config: dict) -> Tuple[bool, str]:
    token = (config.get("api_key") or "").strip()
    if not token:
        return False, "Notion API key is required"
    try:
        from notion_client import Client
    except ImportError:
        return False, "Notion validation requires notion-client (not installed in backend)"
    try:
        Client(auth=token).users.me()
        return True, "Notion connection successful"
    except Exception:
        logger.exception("Notion validation failed")
        return False, "Unable to authenticate with Notion; verify the integration token and workspace access."
