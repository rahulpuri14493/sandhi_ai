"""
Platform tool execution: interactive queries and artifact-first platform writes.

Implementation is split across ``execution_common``, ``execution_sql``,
``execution_object_storage``, ``execution_integrations``, and ``execution_artifact``.
"""
from __future__ import annotations

from execution_artifact import (
    _artifact_write_azure_blob,
    _artifact_write_bigquery,
    _artifact_write_databricks,
    _artifact_write_gcs_blob,
    _artifact_write_mysql,
    _artifact_write_object_store,
    _artifact_write_postgres,
    _artifact_write_snowflake,
    _artifact_write_sqlserver,
    execute_artifact_write,
)
from execution_common import (
    _artifact_object_storage_basename,
    _log_mcp_sql,
    _merge_sql_dialect,
    _postgres_dest_hint,
    _resolve_s3_compatible_endpoint,
    _s3_client_for_config,
    _s3_get_object_bytes,
    _safe_ident,
    _sql_query_from_args,
    _truncate_for_log,
    is_artifact_platform_write,
    parse_artifact_records,
    read_artifact_bytes,
    resolve_local_artifact_path,
)
from execution_integrations import (
    execute_github,
    execute_notion,
    execute_rest_api,
    execute_slack,
)
from execution_object_storage import execute_azure_blob, execute_gcs, execute_s3_family
from execution_sql import (
    execute_bigquery_sql,
    execute_databricks_sql,
    execute_elasticsearch,
    execute_mysql,
    execute_postgres,
    execute_snowflake_sql,
    execute_sqlserver_sql,
)

__all__ = [
    "_artifact_object_storage_basename",
    "_artifact_write_azure_blob",
    "_artifact_write_bigquery",
    "_artifact_write_databricks",
    "_artifact_write_gcs_blob",
    "_artifact_write_mysql",
    "_artifact_write_object_store",
    "_artifact_write_postgres",
    "_artifact_write_snowflake",
    "_artifact_write_sqlserver",
    "_log_mcp_sql",
    "_merge_sql_dialect",
    "_postgres_dest_hint",
    "_resolve_s3_compatible_endpoint",
    "_s3_client_for_config",
    "_s3_get_object_bytes",
    "_safe_ident",
    "_sql_query_from_args",
    "_truncate_for_log",
    "execute_artifact_write",
    "execute_azure_blob",
    "execute_bigquery_sql",
    "execute_databricks_sql",
    "execute_elasticsearch",
    "execute_gcs",
    "execute_github",
    "execute_mysql",
    "execute_notion",
    "execute_postgres",
    "execute_rest_api",
    "execute_s3_family",
    "execute_slack",
    "execute_snowflake_sql",
    "execute_sqlserver_sql",
    "is_artifact_platform_write",
    "parse_artifact_records",
    "read_artifact_bytes",
    "resolve_local_artifact_path",
]
