"""
Validate MCP tool config (connection test) before save.
Does not store any data; only checks connectivity where possible.
"""
from typing import Tuple


def validate_tool_config(tool_type: str, config: dict) -> Tuple[bool, str]:
    """
    Returns (valid, message). valid=True means connection succeeded or validation not implemented.
    """
    if tool_type == "postgres":
        return _validate_postgres(config)
    if tool_type == "mysql":
        return _validate_mysql(config)
    if tool_type == "filesystem":
        return _validate_filesystem(config)
    if tool_type in ("elasticsearch", "rest_api"):
        return _validate_http(config, tool_type)
    if tool_type == "pageindex":
        return _validate_pageindex(config)
    # Vector DB, Slack, GitHub, Notion, S3: no lightweight validation without SDK
    return True, "Connection validation not available for this tool type; save to store credentials."


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
        err_msg = str(e)
        # When backend runs in Docker, localhost refers to the container, not the host.
        if "connection refused" in err_msg.lower() and ("localhost" in conn_str or "127.0.0.1" in conn_str):
            err_msg += " When the app runs in Docker, use host.docker.internal instead of localhost to reach PostgreSQL on your host (e.g. postgresql://postgres:postgres@host.docker.internal:5432/agent_marketplace). To use the Docker Compose Postgres service, use host 'db' (e.g. postgresql://postgres:postgres@db:5432/agent_marketplace)."
        return False, err_msg


def _validate_mysql(config: dict) -> Tuple[bool, str]:
    try:
        import pymysql
        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            user=config.get("user", ""),
            password=config.get("password", ""),
            database=config.get("database", ""),
            connect_timeout=5,
        )
        conn.ping()
        conn.close()
        return True, "MySQL connection successful"
    except ImportError:
        return False, "MySQL validation requires pymysql (not installed in backend)"
    except Exception as e:
        return False, str(e)


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
    except Exception as e:
        return False, str(e)


def _validate_http(config: dict, tool_type: str) -> Tuple[bool, str]:
    import httpx
    url = (config.get("url") or config.get("base_url") or "").strip()
    if not url:
        return False, "URL is required"
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    try:
        r = httpx.get(url.rstrip("/") + "/" if tool_type == "elasticsearch" else url, timeout=5.0)
        if r.status_code < 500:
            return True, "Endpoint reachable"
        return False, f"Endpoint returned {r.status_code}"
    except Exception as e:
        return False, str(e)
