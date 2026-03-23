"""Artifact-first platform writes (job output_contract)."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

import execution_common
from execution_common import (
    _artifact_object_storage_basename,
    _merge_sql_dialect,
    _postgres_dest_hint,
    _resolve_s3_compatible_endpoint,
    _safe_ident,
    _truncate_for_log,
    safe_tool_error,
)

logger = logging.getLogger(__name__)


def _postgres_run_bootstrap_sql(cur: Any, conn: Any, target: Dict[str, Any]) -> Optional[str]:
    """
    Run optional DDL before artifact INSERT (e.g. CREATE TABLE IF NOT EXISTS).
    Set output_contract write_targets[].target.bootstrap_sql to one SQL string or a list of statements.
    Returns an error string on failure, or None on success.
    """
    raw = target.get("bootstrap_sql")
    if raw is None:
        return None
    stmts: List[str]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        stmts = [s]
    elif isinstance(raw, list):
        stmts = [str(x).strip() for x in raw if str(x).strip()]
        if not stmts:
            return None
    else:
        return "Error: target.bootstrap_sql must be a string or a list of SQL strings"
    for stmt in stmts:
        try:
            cur.execute(stmt)
        except Exception as e:
            return safe_tool_error("Postgres bootstrap_sql failed", e)
    conn.commit()
    return None


def _apply_postgres_column_mapping(
    records: List[Dict[str, Any]], target: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Map artifact record keys to DB column names (e.g. agent emits {"content": ...} but table has result_json).

    Set write_targets[].target.column_mapping to {"artifact_key": "db_column", ...}.
    Keys not listed pass through unchanged.
    """
    cm = target.get("column_mapping") or target.get("artifact_column_mapping")
    if not isinstance(cm, dict) or not cm:
        return records
    out: List[Dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        row: Dict[str, Any] = {}
        for k, v in rec.items():
            ks = str(k)
            dest = cm.get(ks, ks)
            row[str(dest)] = v
        out.append(row)
    return out


def _jsonb_column_set(target: Dict[str, Any]) -> set:
    """Column names that use psycopg2.extras.Json so plain text maps to valid JSON/JSONB."""
    raw = target.get("jsonb_columns") or target.get("json_columns")
    if isinstance(raw, list):
        return {str(x) for x in raw}
    if isinstance(raw, str) and raw.strip():
        return {raw.strip()}
    return set()


def _postgres_adapt_cell(col: str, val: Any, jsonb_cols: set) -> Any:
    if col not in jsonb_cols:
        return val
    if val is None:
        return None
    try:
        from psycopg2.extras import Json
    except ImportError:
        return val
    return Json(val)


def execute_artifact_write(tool_type: str, config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    artifact_ref = arguments.get("artifact_ref") or {}
    target = arguments.get("target") or {}
    operation_type = str(arguments.get("operation_type") or "upsert").lower()
    write_mode = str(arguments.get("write_mode") or "upsert").lower()
    merge_keys: List[str] = list(arguments.get("merge_keys") or [])
    idem = str(arguments.get("idempotency_key") or "")[:200]

    try:
        raw = execution_common.read_artifact_bytes(artifact_ref)
    except Exception as e:
        logger.exception("Artifact read failed")
        return f"Error: could not read artifact: {e}"

    fmt = (artifact_ref.get("format") or "jsonl").lower()
    try:
        records = execution_common.parse_artifact_records(raw, fmt)
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
        return _artifact_write_bigquery(config, target, records, merge_keys, operation_type, write_mode)
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
    records = _apply_postgres_column_mapping(records, target)
    if not records or not isinstance(records[0], dict):
        return "Error: artifact contained no row data after column_mapping"
    cols = [str(c) for c in records[0].keys()]
    for k in merge_keys:
        if k not in cols:
            return f"Error: merge_keys must be columns of the artifact; missing {k!r}"
    fq = f'"{_safe_ident(schema)}"."{_safe_ident(table)}"'
    col_sql = ", ".join(f'"{_safe_ident(c)}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    ins = f"INSERT INTO {fq} ({col_sql}) VALUES ({placeholders})"
    jsonb_cols = _jsonb_column_set(target)
    tuples = [
        tuple(_postgres_adapt_cell(c, rec.get(c), jsonb_cols) for c in cols)
        for rec in records
    ]
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
        boot_err = _postgres_run_bootstrap_sql(cur, conn, target)
        if boot_err:
            cur.close()
            conn.close()
            return boot_err
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
            flat.extend([_postgres_adapt_cell(c, rec.get(c), jsonb_cols) for c in cols])
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
            logger.exception("Postgres upsert ON CONFLICT failed")
            return (
                f"Error: Postgres upsert requires a UNIQUE or PRIMARY KEY on ({conflict}). "
                f"({type(e1).__name__})"
            )
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rows": len(records), "mode": "upsert"})
    except Exception as e:
        return safe_tool_error("Postgres artifact write", e)


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
        return safe_tool_error("MySQL artifact write", e)


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
    # Match job output_artifact_format (jsonl | json). Never default to .dat — unknown/missing → jsonl (NDJSON).
    fmt = (artifact_ref.get("format") or "jsonl").strip().lower()
    if fmt not in ("jsonl", "json"):
        fmt = "jsonl"
    ext = ".json" if fmt == "json" else ".jsonl"
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
        return safe_tool_error("Object store artifact write", e)


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
        return safe_tool_error("Snowflake artifact write", e)


def _bq_fq_table(project: str, dataset: str, table: str) -> str:
    """Backtick-quoted table id for BigQuery SQL (MERGE)."""
    if project:
        return f"`{project}.{dataset}.{table}`"
    return f"`{dataset}.{table}`"


def _bq_table_ref(project: str, dataset: str, table: str) -> str:
    """Table id string for load_table_from_json."""
    if project:
        return f"{project}.{dataset}.{table}"
    return f"{dataset}.{table}"


def _artifact_write_bigquery(
    config: Dict[str, Any],
    target: Dict[str, Any],
    records: List[Dict[str, Any]],
    merge_keys: List[str],
    operation_type: str,
    write_mode: str,
) -> str:
    project = (config.get("project_id") or "").strip()
    dataset = (target.get("schema") or target.get("schema_name") or config.get("dataset") or "").strip()
    table = (target.get("table") or "").strip()
    if not dataset or not table:
        return "Error: target schema (dataset) and table are required for BigQuery"

    records = _apply_postgres_column_mapping(records, target)
    if not records or not isinstance(records[0], dict):
        return "Error: artifact contained no row data after column_mapping"
    cols = [str(c) for c in records[0].keys()]
    ot = (operation_type or "upsert").lower()
    wm = (write_mode or "upsert").lower()

    if ot not in ("append", "insert", "upsert", "merge"):
        return f"Error: unsupported operation_type for BigQuery: {operation_type!r}"
    if ot in ("upsert", "merge") and not merge_keys:
        return (
            "Error: BigQuery upsert/merge requires merge_keys in the output contract. "
            "The destination table is not truncated for upsert/merge (that would delete unrelated rows). "
            "For a full table replace, use operation_type append or insert with write_mode overwrite and no merge_keys."
        )
    for k in merge_keys:
        if k not in cols:
            return f"Error: merge_keys must be columns of the artifact; missing {k!r}"

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return "Error: google-cloud-bigquery is not installed"

    creds_json = config.get("credentials_json")
    if creds_json:
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(project=project, credentials=creds)
    else:
        client = bigquery.Client(project=project or None)

    table_ref = _bq_table_ref(project, dataset, table)
    fq_target = _bq_fq_table(project, dataset, table)

    # Keyed upsert: load to a staging table, then MERGE into the target (no blind TRUNCATE).
    if ot in ("upsert", "merge"):
        stg = f"_mcp_stg_{uuid.uuid4().hex[:16]}"
        stg_ref = _bq_table_ref(project, dataset, stg)
        fq_stg = _bq_fq_table(project, dataset, stg)
        try:
            job_config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
                schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            )
            load_job = client.load_table_from_json(records, stg_ref, job_config=job_config)
            load_job.result()
            merge_sql = _merge_sql_dialect("bigquery", fq_target, cols, merge_keys, fq_stg)
            logger.info(
                "MCP artifact BigQuery MERGE dest=%s rows=%s merge_keys=%s",
                table_ref,
                len(records),
                merge_keys,
            )
            qjob = client.query(merge_sql)
            qjob.result()
            client.delete_table(stg_ref, not_found_ok=True)
            return json.dumps(
                {"status": "ok", "rows": len(records), "table": table_ref, "operation": "merge"}
            )
        except Exception as e:
            try:
                client.delete_table(stg_ref, not_found_ok=True)
            except Exception:
                pass
            return safe_tool_error("BigQuery MERGE artifact write", e)

    # Append / insert only: append, or explicit full replace via overwrite + no merge_keys.
    try:
        if wm == "overwrite" and ot in ("append", "insert") and not merge_keys:
            wd = bigquery.WriteDisposition.WRITE_TRUNCATE
        else:
            wd = bigquery.WriteDisposition.WRITE_APPEND
        job_config = bigquery.LoadJobConfig(
            write_disposition=wd,
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        )
        job = client.load_table_from_json(records, table_ref, job_config=job_config)
        job.result()
        return json.dumps({"status": "ok", "rows": len(records), "table": table_ref})
    except Exception as e:
        return safe_tool_error("BigQuery artifact write", e)


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
        return safe_tool_error("SQL Server artifact write", e)


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
        # Insert every row; commit periodically so very large artifacts do not rely on one huge transaction.
        _DATABRICKS_COMMIT_EVERY = 5000
        inserted = 0
        for rec in records:
            vals = ", ".join(_sql_literal(rec.get(c)) for c in cols)
            cur.execute(f"INSERT INTO {fq} ({', '.join(cols)}) VALUES ({vals})")
            inserted += 1
            if inserted % _DATABRICKS_COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "ok", "rows": inserted})
    except Exception as e:
        return safe_tool_error("Databricks artifact write", e)


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
        return safe_tool_error("Azure blob artifact write", e)


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
        return safe_tool_error("GCS blob artifact write", e)
