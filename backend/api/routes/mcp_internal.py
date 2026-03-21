"""
Internal MCP API: used by the platform MCP server only.
Returns tool list and decrypted config for a business (tenant).
Protected by MCP_INTERNAL_SECRET header.
"""
import json
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from db.database import get_db
from models.mcp_server import MCPToolConfig, MCPToolType
from models.audit_log import AuditLog
from core.encryption import decrypt_json
from core.config import settings

router = APIRouter(prefix="/api/internal/mcp", tags=["mcp-internal"])

MCP_SECRET_HEADER = "x-internal-secret"


def _verify_internal_secret(x_internal_secret: Optional[str] = Header(None, alias=MCP_SECRET_HEADER)):
    if not settings.MCP_INTERNAL_SECRET:
        raise HTTPException(status_code=503, detail="MCP internal API not configured")
    if x_internal_secret != settings.MCP_INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid or missing internal secret")
    return x_internal_secret


class ToolConfigRequest(BaseModel):
    business_id: int


@router.get("/tools")
def internal_list_tools(
    business_id: int,
    _: str = Depends(_verify_internal_secret),
    db: Session = Depends(get_db),
):
    """
    List platform MCP tools for a business (tenant).
    Returns tool id, name, type, and inputSchema for MCP tools/list.
    """
    rows = db.query(MCPToolConfig).filter(
        MCPToolConfig.user_id == business_id,
        MCPToolConfig.is_active == True,
    ).order_by(MCPToolConfig.name).all()
    tools = []
    for t in rows:
        # MCP tool descriptor (no credentials)
        schema = _input_schema_for_type(t.tool_type)
        entry = {
            "id": t.id,
            "name": _tool_name(t.id, t.name),
            "description": _description_for_type(t.tool_type, t.name),
            "inputSchema": schema,
        }
        if getattr(t, "schema_metadata", None):
            entry["schemaMetadata"] = t.schema_metadata
        if getattr(t, "business_description", None):
            entry["businessDescription"] = t.business_description
        tools.append(entry)
    return {"tools": tools}


def _tool_name(tool_id: int, name: str) -> str:
    """Stable tool name for MCP (platform prefix + id to avoid collisions)."""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in name.strip())[:50]
    return f"platform_{tool_id}_{safe}" if safe else f"platform_{tool_id}"


def _description_for_type(tool_type: MCPToolType, name: str) -> str:
    d = {
        MCPToolType.VECTOR_DB: "Query vector database",
        MCPToolType.PINECONE: "Query Pinecone vector index",
        MCPToolType.WEAVIATE: "Query Weaviate vector store",
        MCPToolType.QDRANT: "Query Qdrant vector database",
        MCPToolType.CHROMA: "Query Chroma vector store",
        MCPToolType.POSTGRES: "Execute read-only PostgreSQL query",
        MCPToolType.MYSQL: "Execute read-only MySQL query",
        MCPToolType.SQLSERVER: "Execute read and write SQL Server operations",
        MCPToolType.SNOWFLAKE: "Execute read and write Snowflake operations",
        MCPToolType.DATABRICKS: "Execute read and write Databricks operations",
        MCPToolType.BIGQUERY: "Execute read and write BigQuery operations",
        MCPToolType.ELASTICSEARCH: "Search Elasticsearch index",
        MCPToolType.PAGEINDEX: "Query PageIndex (vectorless document retrieval)",
        MCPToolType.FILESYSTEM: "Read or list files in configured base path",
        MCPToolType.S3: "Read or list objects in S3 bucket",
        MCPToolType.MINIO: "Read or write objects in MinIO bucket",
        MCPToolType.CEPH: "Read or write objects in Ceph object storage",
        MCPToolType.AZURE_BLOB: "Read or write objects in Azure Blob storage",
        MCPToolType.GCS: "Read or write objects in Google Cloud Storage",
        MCPToolType.SLACK: "Send message or list channels (Slack)",
        MCPToolType.GITHUB: "Query GitHub repos, issues, or files",
        MCPToolType.NOTION: "Query or search Notion workspace",
        MCPToolType.REST_API: "Call external REST API",
    }
    return f"{d.get(tool_type, tool_type.value)}: {name}"


def _input_schema_for_type(tool_type: MCPToolType) -> dict:
    vector_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query or embedding query"},
            "top_k": {"type": "integer", "description": "Max results", "default": 5},
        },
        "required": ["query"],
    }
    sql_schema = {
        "type": "object",
        "properties": {
            "operation_type": {
                "type": "string",
                "enum": ["read", "insert", "update", "upsert", "merge"],
                "default": "read",
            },
            "query": {"type": "string", "description": "SQL query (read or write as allowed by policy)"},
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
            "write_mode": {"type": "string", "enum": ["append", "overwrite", "upsert", "merge"], "default": "upsert"},
            "merge_keys": {"type": "array", "items": {"type": "string"}},
            "idempotency_key": {"type": "string"},
        },
        "required": ["query"],
    }
    schemas = {
        MCPToolType.VECTOR_DB: vector_schema,
        MCPToolType.PINECONE: vector_schema,
        MCPToolType.WEAVIATE: vector_schema,
        MCPToolType.QDRANT: vector_schema,
        MCPToolType.CHROMA: vector_schema,
        MCPToolType.POSTGRES: sql_schema,
        MCPToolType.MYSQL: sql_schema,
        MCPToolType.SQLSERVER: sql_schema,
        MCPToolType.SNOWFLAKE: sql_schema,
        MCPToolType.DATABRICKS: sql_schema,
        MCPToolType.BIGQUERY: sql_schema,
        MCPToolType.ELASTICSEARCH: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "index": {"type": "string", "description": "Index name"},
                "size": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        MCPToolType.PAGEINDEX: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language or keyword query over documents"},
                "doc_id": {"type": "string", "description": "PageIndex document ID (optional if default_doc_id is set in config)"},
                "thinking": {"type": "boolean", "description": "Use reasoning before retrieval", "default": False},
            },
            "required": ["query"],
        },
        MCPToolType.FILESYSTEM: {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path under base_path"},
                "action": {"type": "string", "enum": ["read", "list"], "default": "read"},
            },
            "required": ["path"],
        },
        MCPToolType.S3: {
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
            },
            "required": ["key"],
        },
        MCPToolType.MINIO: {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Object key or prefix"},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["key"],
        },
        MCPToolType.CEPH: {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Object key or prefix"},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["key"],
        },
        MCPToolType.AZURE_BLOB: {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Blob name or prefix"},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["key"],
        },
        MCPToolType.GCS: {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Object key or prefix"},
                "artifact_ref": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string"}}},
                "action": {"type": "string", "enum": ["get", "list", "put", "write"], "default": "get"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["key"],
        },
        MCPToolType.SLACK: {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name"},
                "message": {"type": "string", "description": "Message text"},
                "action": {"type": "string", "enum": ["list_channels", "send"], "default": "send"},
            },
        },
        MCPToolType.GITHUB: {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo"},
                "path": {"type": "string", "description": "File path or 'issues'"},
                "action": {"type": "string", "enum": ["get_file", "list_issues", "search"], "default": "get_file"},
            },
        },
        MCPToolType.NOTION: {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "get_page", "get_database"], "default": "search"},
                "query": {"type": "string", "description": "Search query or page/database ID"},
            },
        },
        MCPToolType.REST_API: {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                "path": {"type": "string", "description": "Path or full URL"},
                "body": {"type": "object", "description": "JSON body for POST/PUT/PATCH"},
            },
            "required": ["path"],
        },
    }
    return schemas.get(tool_type, {"type": "object", "properties": {}})


@router.post("/tools/{tool_id}/config")
def internal_get_tool_config(
    tool_id: int,
    body: ToolConfigRequest,
    _: str = Depends(_verify_internal_secret),
    db: Session = Depends(get_db),
):
    """
    Return decrypted config for a platform tool. Caller (platform MCP server) must
    use this to execute the tool. Only tools belonging to business_id are allowed.
    """
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == body.business_id,
        MCPToolConfig.is_active == True,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool not found or access denied")
    config = decrypt_json(t.encrypted_config)
    # Observability: log tool config fetch (tool invocation path)
    log_entry = AuditLog(
        entity_type="mcp",
        entity_id=t.id,
        action="tool_config_fetched",
        details=json.dumps({"business_id": body.business_id, "tool_type": t.tool_type.value}),
    )
    db.add(log_entry)
    db.commit()
    return {
        "tool_id": t.id,
        "tool_type": t.tool_type.value,
        "name": t.name,
        "config": config,
    }
