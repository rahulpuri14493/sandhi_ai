"""
JSON Schema for platform MCP tool parameters (OpenAI function calling + tools/list).

Single source of truth for:
- GET /api/internal/mcp/tools (platform MCP server tools/list)
- Agent executor → A2A metadata openai_tools

Postgres/MySQL/SQL backends use a minimal arguments schema (optional ``params`` only) so
strict validators do not reject artifact-only calls. SQL text is defined on the tool
configuration (operator-controlled), not in request arguments; use job ``output_contract``
artifact writes for table loads and controlled DML.
"""
from __future__ import annotations

from typing import Any, Dict


def input_schema_for_platform_tool_type(tool_type: str) -> Dict[str, Any]:
    """Return input JSON Schema for a platform tool type string (e.g. postgres, chroma)."""
    vector_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query or embedding query"},
            "top_k": {"type": "integer", "description": "Max results", "default": 5},
        },
        "required": ["query"],
    }
    weaviate_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search text: uses Weaviate near_text when the collection has a server vectorizer, "
                    "else BM25 keyword search when text properties are indexed, "
                    "else near_vector if openai_api_key is set on this tool."
                ),
            },
            "top_k": dict(vector_schema["properties"]["top_k"]),
        },
        "required": ["query"],
    }
    pinecone_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            **dict(vector_schema["properties"]),
            "namespace": {
                "type": "string",
                "description": "Pinecone namespace (omit for __default__).",
            },
            "fields": {
                "description": "Field names to return from integrated text search (e.g. text, chunk_text). JSON array or comma-separated.",
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ],
            },
        },
        "required": ["query"],
    }
    chroma_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Text to search for (vector similarity, not guaranteed exact keyword/email match). "
                    "In the tool result, use each match's **sender** and **metadata** to identify who a message is from. "
                    "On **Chroma Cloud**, the server embeds the query (same as the dashboard; e.g. Qwen)—no OpenAI key needed "
                    "for that path. On **self-hosted** Chroma without server embedding, configure **openai_api_key** on this tool. "
                    "Ensure the tool's collection name matches the Chroma UI exactly."
                ),
            },
            "top_k": dict(vector_schema["properties"]["top_k"]),
        },
        "required": ["query"],
    }
    # Strict: no extra keys — models often confuse job output_contract fields (write_mode, target) with SQL tools.
    sql_schema_interactive: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional runtime SQL. Allowed only for a strict single read-only SELECT/WITH statement.",
            },
            "sql": {
                "type": "string",
                "description": "Alias of query (read-only SELECT/WITH only).",
            },
            "statement": {
                "type": "string",
                "description": "Alias of query (read-only SELECT/WITH only).",
            },
            "params": {
                "type": "array",
                "description": "Optional bound parameters for configured SQL or runtime read-only query.",
                "items": {},
            },
        },
        "required": [],
        "additionalProperties": False,
    }
    sql_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation_type": {
                "type": "string",
                "description": "For artifact/job writes: read | insert | update | upsert | merge",
            },
            "query": {
                "type": "string",
                "description": "Deprecated in arguments; define SQL on the tool configuration. Optional for legacy clients.",
            },
            "artifact_ref": {
                "type": "object",
                "properties": {
                    "storage": {"type": "string"},
                    "path": {"type": "string"},
                    "format": {"type": "string"},
                },
            },
            "target": {
                "type": "object",
                "properties": {
                    "database": {"type": "string"},
                    "schema": {"type": "string"},
                    "table": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
            "write_mode": {
                "type": "string",
                "description": "Artifact writes: append | overwrite | upsert | merge (default upsert if omitted)",
            },
            "merge_keys": {"type": "array", "items": {"type": "string"}},
            "idempotency_key": {"type": "string"},
        },
        "required": [],
        "additionalProperties": True,
    }
    tt = (tool_type or "").strip().lower()
    schemas: Dict[str, Dict[str, Any]] = {
        "vector_db": vector_schema,
        "pinecone": pinecone_schema,
        "weaviate": weaviate_schema,
        "qdrant": vector_schema,
        "chroma": chroma_schema,
        "postgres": sql_schema_interactive,
        "mysql": sql_schema_interactive,
        "sqlserver": sql_schema,
        "snowflake": sql_schema,
        "databricks": sql_schema,
        "bigquery": sql_schema,
        "elasticsearch": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "index": {"type": "string", "description": "Index name"},
                "size": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        "pageindex": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language or keyword query over documents"},
                "doc_id": {"type": "string", "description": "PageIndex document ID (optional if default_doc_id is set in config)"},
                "thinking": {"type": "boolean", "description": "Use reasoning before retrieval", "default": False},
            },
            "required": ["query"],
        },
        "filesystem": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path under base_path"},
                "action": {"type": "string", "enum": ["read", "list", "write"], "default": "read"},
                "content": {"type": "string", "description": "File content when action is write"},
            },
            "required": ["path"],
        },
        "s3": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Object key (get/put) or folder prefix for list. Do not prefix with the bucket name "
                        "or s3://bucket/ — the tool already targets one bucket. For action=list, use '' for "
                        "bucket root (first page). If response is_truncated is true, call list again with "
                        "continuation_token set to next_continuation_token."
                    ),
                },
                "continuation_token": {
                    "type": "string",
                    "description": "For action=list only: S3 ListObjectsV2 ContinuationToken from a previous truncated response.",
                },
                "max_keys": {
                    "type": "integer",
                    "description": "For action=list: max keys per page (default 500, cap 5000). Alias: max_results.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Alias of max_keys for action=list.",
                },
                "source_prefix": {
                    "type": "string",
                    "description": "For action=copy_prefix: source key prefix (folder); trailing slash optional.",
                },
                "dest_prefix": {
                    "type": "string",
                    "description": "For action=copy_prefix: destination prefix under the same bucket; must satisfy write_key_prefix.",
                },
                "destination_prefix": {
                    "type": "string",
                    "description": "Alias of dest_prefix for action=copy_prefix.",
                },
                "max_objects": {
                    "type": "integer",
                    "description": (
                        "For copy_prefix: max objects per call (capped by MCP_S3_COPY_PREFIX_MAX_OBJECTS). "
                        "Not transactional: on failure some keys may already be copied — response includes "
                        "idempotency_and_resume, next_continuation_token, next_start_after, or copy_failed details."
                    ),
                },
                "start_after": {
                    "type": "string",
                    "description": "For action=copy_prefix: S3 StartAfter when resuming after max_objects (use next_start_after from prior response).",
                },
                "copy_start_after": {"type": "string", "description": "Alias of start_after for copy_prefix resume."},
                "max_read_bytes": {
                    "type": "integer",
                    "description": "For get/read: per-request read cap (capped by server MCP_OBJECT_STORAGE_MAX_READ_BYTES).",
                },
                "read_offset": {
                    "type": "integer",
                    "description": "For get/read: start byte (with read_length or byte_range). Returns JSON with text or bytes_b64.",
                },
                "read_length": {
                    "type": "integer",
                    "description": "For get/read: number of bytes to read from read_offset (capped by max_read_bytes).",
                },
                "byte_range": {
                    "type": "string",
                    "description": "For get/read: inclusive range \"start-end\" (e.g. \"0-1048575\"); open end uses max_read_bytes from start.",
                },
                "artifact_ref": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "format": {"type": "string"},
                    },
                },
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "put", "write", "copy_prefix"],
                    "default": "get",
                },
                "body": {
                    "type": "string",
                    "description": "Required for put/write: full object payload as string (e.g. JSONL lines or text). Same tool call must include this.",
                },
                "content": {
                    "type": "string",
                    "description": "Alias for body on put/write.",
                },
            },
            "required": ["key"],
        },
        "minio": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Object key (get/put) or folder prefix for list. Do not prefix with the bucket name "
                        "or s3://bucket/. For list pagination use continuation_token from prior response."
                    ),
                },
                "continuation_token": {
                    "type": "string",
                    "description": "For action=list: continuation token when is_truncated was true.",
                },
                "max_keys": {"type": "integer", "description": "For action=list: page size (default 500, cap 5000)."},
                "max_results": {"type": "integer", "description": "Alias of max_keys for list."},
                "source_prefix": {"type": "string", "description": "For copy_prefix: source prefix (or use key)."},
                "dest_prefix": {"type": "string", "description": "For copy_prefix: destination prefix (same bucket)."},
                "destination_prefix": {"type": "string", "description": "Alias of dest_prefix."},
                "max_objects": {"type": "integer", "description": "For copy_prefix: max copies per call (server-capped)."},
                "start_after": {"type": "string", "description": "For copy_prefix resume: next_start_after from prior response."},
                "copy_start_after": {"type": "string", "description": "Alias of start_after."},
                "max_read_bytes": {"type": "integer", "description": "For get/read: cap (≤ MCP_OBJECT_STORAGE_MAX_READ_BYTES)."},
                "read_offset": {"type": "integer", "description": "For get/read: start byte with read_length or byte_range."},
                "read_length": {"type": "integer", "description": "For get/read: byte count from read_offset."},
                "byte_range": {"type": "string", "description": "For get/read: inclusive \"start-end\" range."},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "put", "write", "copy_prefix"],
                    "default": "get",
                },
                "idempotency_key": {"type": "string"},
                "body": {
                    "type": "string",
                    "description": "Required for put/write (interactive MinIO): full object payload as string (e.g. JSONL). Models often omit this and trigger errors — always send body or content in the same tool call.",
                },
                "content": {
                    "type": "string",
                    "description": "Alias for body on put/write.",
                },
            },
            "required": ["key"],
        },
        "ceph": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "S3-compatible object key (get/put) or list prefix. Same rules as MinIO/S3: no leading "
                        "bucket/ or s3://bucket/ prefix; use continuation_token for long listings."
                    ),
                },
                "continuation_token": {
                    "type": "string",
                    "description": "For action=list: continuation token when is_truncated was true.",
                },
                "max_keys": {"type": "integer", "description": "For action=list: page size (default 500, cap 5000)."},
                "max_results": {"type": "integer", "description": "Alias of max_keys for list."},
                "source_prefix": {"type": "string", "description": "For copy_prefix: source prefix (or use key)."},
                "dest_prefix": {"type": "string", "description": "For copy_prefix: destination prefix (same bucket)."},
                "destination_prefix": {"type": "string", "description": "Alias of dest_prefix."},
                "max_objects": {"type": "integer", "description": "For copy_prefix: max copies per call (server-capped)."},
                "start_after": {"type": "string", "description": "For copy_prefix resume: next_start_after from prior response."},
                "copy_start_after": {"type": "string", "description": "Alias of start_after."},
                "max_read_bytes": {"type": "integer", "description": "For get/read: cap (≤ MCP_OBJECT_STORAGE_MAX_READ_BYTES)."},
                "read_offset": {"type": "integer", "description": "For get/read: start byte with read_length or byte_range."},
                "read_length": {"type": "integer", "description": "For get/read: byte count from read_offset."},
                "byte_range": {"type": "string", "description": "For get/read: inclusive \"start-end\" range."},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "put", "write", "copy_prefix"],
                    "default": "get",
                },
                "idempotency_key": {"type": "string"},
                "body": {"type": "string", "description": "Required for put/write: object payload string."},
                "content": {"type": "string", "description": "Alias for body on put/write."},
            },
            "required": ["key"],
        },
        "azure_blob": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Blob name (get/put) or prefix for list (name_starts_with). Do not start with '/' — "
                        "Azure names are never root-absolute. For a folder, use e.g. reports/summaries/. "
                        "For action=list at container root use empty string."
                    ),
                },
                "continuation_token": {
                    "type": "string",
                    "description": "For action=list: next_continuation_token from a truncated prior response.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "For action=list: blobs per page (default 500, cap 5000).",
                },
                "max_read_bytes": {"type": "integer", "description": "For get/read: cap (≤ MCP_OBJECT_STORAGE_MAX_READ_BYTES)."},
                "read_offset": {"type": "integer", "description": "For get/read: start byte with read_length or byte_range."},
                "read_length": {"type": "integer", "description": "For get/read: byte count from read_offset."},
                "byte_range": {"type": "string", "description": "For get/read: inclusive \"start-end\" range."},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
                "body": {"type": "string", "description": "Required for put/write: blob payload string."},
                "content": {"type": "string", "description": "Alias for body on put/write."},
            },
            "required": ["key"],
        },
        "gcs": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Object name inside the configured bucket (get/put) or prefix for list. "
                        "Do not prefix with the bucket name. For action=list at bucket root use empty string. "
                        "If is_truncated is true, call list again with page_token=next_page_token."
                    ),
                },
                "page_token": {
                    "type": "string",
                    "description": "For action=list: GCS list_blobs page_token from a previous response (next_page_token).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "For action=list: max objects per page (default 500, cap 5000).",
                },
                "max_read_bytes": {"type": "integer", "description": "For get/read: cap (≤ MCP_OBJECT_STORAGE_MAX_READ_BYTES)."},
                "read_offset": {"type": "integer", "description": "For get/read: start byte with read_length or byte_range."},
                "read_length": {"type": "integer", "description": "For get/read: byte count from read_offset."},
                "byte_range": {"type": "string", "description": "For get/read: inclusive \"start-end\" range."},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
                "body": {"type": "string", "description": "Required for put/write: object payload string."},
                "content": {"type": "string", "description": "Alias for body on put/write."},
            },
            "required": ["key"],
        },
        "slack": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name"},
                "message": {"type": "string", "description": "Message text"},
                "action": {
                    "type": "string",
                    "enum": ["list_channels", "list_messages", "send"],
                    "default": "send",
                },
                "limit": {
                    "type": "integer",
                    "description": "For list_messages: max messages (1–200, default 50).",
                },
                "cursor": {
                    "type": "string",
                    "description": "For list_messages: pagination cursor from previous next_cursor.",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Required for action send unless PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY=true. "
                        "Stable unique key per logical post; identical key+payload returns cached success within TTL (Redis when configured)."
                    ),
                },
            },
        },
        "smtp": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["validate", "send", "list_mail_messages", "get_mail_message", "get_mail_attachment"],
                    "default": "send",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail list_mail_messages: optional Gmail search query (same syntax as Gmail UI).",
                },
                "q": {"type": "string", "description": "Alias for query on list_mail_messages."},
                "max_results": {
                    "type": "integer",
                    "description": "Gmail list_mail_messages: max IDs to return (1–50, default 20).",
                },
                "message_id": {
                    "type": "string",
                    "description": "Gmail get_mail_message / get_mail_attachment: message id from list_mail_messages.",
                },
                "id": {"type": "string", "description": "Alias for message_id (Gmail)."},
                "attachment_id": {
                    "type": "string",
                    "description": "Gmail get_mail_attachment: attachment_id from get_mail_message attachments list.",
                },
                "to": {
                    "description": "Recipient(s): comma/semicolon-separated or array of addresses.",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "cc": {
                    "description": "Optional CC addresses (string or array).",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "bcc": {
                    "description": "Optional BCC addresses (string or array).",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body"},
                "html_body": {"type": "string", "description": "Optional HTML body (multipart/alternative if body+html_body)."},
                "from_address": {"type": "string", "description": "Override From; defaults to tool config."},
                "from_name": {"type": "string"},
                "attachments": {
                    "type": "array",
                    "description": "Optional file attachments (base64). Max 10 files, 5 MiB each, 12 MiB total decoded.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "content_base64": {"type": "string"},
                            "content_type": {"type": "string", "description": "MIME type, default application/octet-stream"},
                        },
                        "required": ["content_base64"],
                    },
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Required for action send unless PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY=true. "
                        "Dedupes successful sends within TTL (Redis when configured, else in-process on MCP server)."
                    ),
                },
            },
        },
        "teams": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_joined_teams",
                        "list_channels",
                        "list_channel_messages",
                        "get_channel_message",
                        "list_mail_messages",
                        "get_mail_message",
                        "get_mail_attachment",
                        "send_message",
                        "reply_message",
                    ],
                    "default": "list_joined_teams",
                },
                "team_id": {
                    "type": "string",
                    "description": "Required for list_channels, channel message actions, send_message, reply_message.",
                },
                "channel_id": {
                    "type": "string",
                    "description": "Required for channel messages, send_message, reply_message.",
                },
                "message_id": {
                    "type": "string",
                    "description": "reply_message / get_channel_message: Graph message id. get_mail_message / get_mail_attachment: Graph mail message id.",
                },
                "top": {
                    "type": "integer",
                    "description": "list_channel_messages / list_mail_messages: page size (Teams mail default 15, channel default 25; max 50).",
                },
                "mail_folder": {
                    "type": "string",
                    "description": "list_mail_messages: mail folder id or well-known name (default inbox).",
                },
                "folder": {"type": "string", "description": "Alias for mail_folder."},
                "include_full_body": {
                    "type": "boolean",
                    "description": "get_mail_message: if true, include full MIME body (truncated); else bodyPreview-style excerpt.",
                },
                "full_body": {"type": "boolean", "description": "Alias for include_full_body."},
                "attachment_id": {
                    "type": "string",
                    "description": "get_mail_attachment: Graph attachment id from the message.",
                },
                "body": {"type": "string", "description": "Message text (or use text / message alias)."},
                "text": {"type": "string"},
                "message": {"type": "string"},
                "content_type": {"type": "string", "enum": ["text", "html"], "default": "text"},
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Required for send_message and reply_message unless PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY=true. "
                        "Dedupes successful Graph posts within TTL."
                    ),
                },
            },
        },
        "github": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo"},
                "path": {"type": "string", "description": "File path or 'issues'"},
                "action": {"type": "string", "enum": ["get_file", "list_issues", "search"], "default": "get_file"},
            },
        },
        "notion": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "get_page", "get_database"], "default": "search"},
                "query": {"type": "string", "description": "Search query or page/database ID"},
            },
        },
        "rest_api": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                "path": {
                    "type": "string",
                    "description": "Relative path only (no scheme, no leading slash); combined with base_url from tool config.",
                },
                "body": {"type": "object", "description": "JSON body for POST/PUT/PATCH"},
            },
            "required": ["path"],
        },
    }
    return schemas.get(tt, {"type": "object", "properties": {}})
