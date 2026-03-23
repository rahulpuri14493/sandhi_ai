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
        "pinecone": vector_schema,
        "weaviate": vector_schema,
        "qdrant": vector_schema,
        "chroma": vector_schema,
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
                "key": {"type": "string", "description": "Object key or prefix"},
                "artifact_ref": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "format": {"type": "string"},
                    },
                },
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
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
                "key": {"type": "string", "description": "Object key or prefix"},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
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
                "key": {"type": "string", "description": "Object key or prefix"},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
                "body": {"type": "string", "description": "Required for put/write: object payload string."},
                "content": {"type": "string", "description": "Alias for body on put/write."},
            },
            "required": ["key"],
        },
        "azure_blob": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Blob name or prefix"},
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
                "key": {"type": "string", "description": "Object key or prefix"},
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
                "action": {"type": "string", "enum": ["list_channels", "send"], "default": "send"},
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
                "path": {"type": "string", "description": "Path or full URL"},
                "body": {"type": "object", "description": "JSON body for POST/PUT/PATCH"},
            },
            "required": ["path"],
        },
    }
    return schemas.get(tt, {"type": "object", "properties": {}})
