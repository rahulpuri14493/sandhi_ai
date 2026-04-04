"""
Internal MCP API: used by the platform MCP server only.
Returns tool list and decrypted config for a business (tenant).
Protected by MCP_INTERNAL_SECRET header.
"""
import json
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from db.database import get_db
from models.mcp_server import MCPToolConfig, MCPToolType
from models.audit_log import AuditLog
from core.encryption import decrypt_json
from core.config import settings
from services.mcp_tool_input_schemas import input_schema_for_platform_tool_type

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
    if business_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid business_id")
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
        MCPToolType.VECTOR_DB: "Query vector database (read/search; writes via provider or job flows)",
        MCPToolType.PINECONE: "Query Pinecone vector index (read/search; writes via provider or job flows)",
        MCPToolType.WEAVIATE: "Query Weaviate vector store (read/search; writes via provider or job flows)",
        MCPToolType.QDRANT: "Query Qdrant vector database (read/search; writes via provider or job flows)",
        MCPToolType.CHROMA: (
            "Query Chroma vector store (read/search; writes via provider or job flows). "
            "Scoped to this Sandhi user's MCP tool only—config and credentials are not shared across accounts. "
            "Results are similarity-ranked; each hit includes `sender` when metadata has from/sender/email-style fields."
        ),
        MCPToolType.POSTGRES: "Execute PostgreSQL SQL (reads and writes)",
        MCPToolType.MYSQL: "Execute MySQL SQL (reads and writes)",
        MCPToolType.SQLSERVER: "Execute SQL Server SQL (reads and writes)",
        MCPToolType.SNOWFLAKE: "Execute Snowflake SQL (reads and writes)",
        MCPToolType.DATABRICKS: "Execute Databricks SQL (reads and writes)",
        MCPToolType.BIGQUERY: "Execute BigQuery SQL (reads and writes)",
        MCPToolType.ELASTICSEARCH: "Search Elasticsearch index (read); index updates via ES APIs or job flows",
        MCPToolType.PAGEINDEX: "Query PageIndex documents (read/search; writes via PageIndex APIs where applicable)",
        MCPToolType.FILESYSTEM: "Read, list, and write files under configured base path",
        MCPToolType.S3: "Read, list, and write objects in S3 bucket",
        MCPToolType.MINIO: "Read, list, and write objects in MinIO bucket",
        MCPToolType.CEPH: "Read, list, and write objects in Ceph object storage",
        MCPToolType.AZURE_BLOB: "Read, list, and write objects in Azure Blob storage",
        MCPToolType.GCS: "Read, list, and write objects in Google Cloud Storage",
        MCPToolType.SLACK: "List channels and send messages (Slack)",
        MCPToolType.GITHUB: "Read GitHub repos, issues, and files (read-only in platform MCP)",
        MCPToolType.NOTION: "Search and retrieve Notion pages and databases (read-only in platform MCP)",
        MCPToolType.REST_API: "Call external REST API (reads and writes)",
    }
    return f"{d.get(tool_type, tool_type.value)}: {name}"


def _input_schema_for_type(tool_type: MCPToolType) -> dict:
    return input_schema_for_platform_tool_type(tool_type.value)


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
    if body.business_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid business_id")
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
