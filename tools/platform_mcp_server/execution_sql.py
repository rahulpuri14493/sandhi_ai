"""SQL and Elasticsearch execution for platform MCP."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from execution_common import (
    _log_mcp_sql,
    _postgres_dest_hint,
    _sql_query_from_args,
)

logger = logging.getLogger(__name__)

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
    interactive_readonly = bool(config.get("interactive_readonly")) or (
        os.environ.get("MCP_POSTGRES_INTERACTIVE_READONLY", "").strip().lower() in ("1", "true", "yes")
    )
    if interactive_readonly and not is_read_query:
        return (
            "Error: interactive Postgres is read-only for this tool "
            "(set interactive_readonly on the MCP tool config or MCP_POSTGRES_INTERACTIVE_READONLY=1). "
            "Only SELECT (and read-only WITH) queries are allowed. "
            "Use output_contract platform writes for controlled INSERT/DDL to named tables."
        )
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
