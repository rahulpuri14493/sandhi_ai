"""
MCP (Model Context Protocol) API: connections and tool configs per user.
Credentials stored encrypted; platform talks to MCP server via API (JSON-RPC proxy).
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from db.database import get_db
from models.user import User
from models.audit_log import AuditLog
from models.mcp_server import MCPServerConnection, MCPToolConfig, MCPToolType
from schemas.mcp import (
    MCPServerConnectionCreate,
    MCPServerConnectionUpdate,
    MCPServerConnectionResponse,
    MCPToolConfigCreate,
    MCPToolConfigUpdate,
    MCPToolConfigResponse,
    MCPProxyRequest,
    ValidateToolConfigRequest,
)
from core.security import get_current_business_user
from core.encryption import encrypt_json, decrypt_json

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _connection_to_response(c: MCPServerConnection) -> MCPServerConnectionResponse:
    return MCPServerConnectionResponse(
        id=c.id,
        user_id=c.user_id,
        name=c.name,
        base_url=c.base_url,
        endpoint_path=c.endpoint_path or "/mcp",
        auth_type=c.auth_type,
        is_platform_configured=c.is_platform_configured,
        is_active=c.is_active,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _tool_to_response(t: MCPToolConfig) -> MCPToolConfigResponse:
    schema_table_count = None
    if t.schema_metadata:
        try:
            data = json.loads(t.schema_metadata)
            schema_table_count = len(data.get("tables", []))
        except (TypeError, json.JSONDecodeError):
            pass
    return MCPToolConfigResponse(
        id=t.id,
        user_id=t.user_id,
        tool_type=t.tool_type.value,
        name=t.name,
        is_active=t.is_active,
        business_description=getattr(t, "business_description", None) or None,
        schema_metadata=t.schema_metadata,
        schema_table_count=schema_table_count,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# --- Server connections (connect existing MCP server) ---


@router.post("/connections/validate")
async def validate_connection(
    body: MCPServerConnectionCreate,
    current_user: User = Depends(get_current_business_user),
):
    """
    Test MCP server connectivity (JSON-RPC initialize) without saving.
    Returns { "valid": true, "message": "..." } or { "valid": false, "message": "..." }.
    """
    from services.mcp_client import call_mcp_server
    base_url = (body.base_url or "").strip().rstrip("/")
    if not base_url:
        return {"valid": False, "message": "Server URL is required"}
    endpoint_path = (body.endpoint_path or "/mcp").strip()
    if not endpoint_path.startswith("/"):
        endpoint_path = "/" + endpoint_path
    auth_type = body.auth_type or "none"
    credentials = body.credentials
    try:
        await call_mcp_server(
            base_url=base_url,
            endpoint_path=endpoint_path,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sandhi-ai-mcp-validate", "version": "1.0.0"},
            },
            auth_type=auth_type,
            credentials=credentials,
            timeout=15.0,
        )
        return {"valid": True, "message": "MCP server connection successful"}
    except Exception as e:
        logging.exception("MCP server connection validation failed for base_url=%s, endpoint_path=%s", base_url, endpoint_path)
        return {
            "valid": False,
            "message": "Failed to connect to MCP server. Please verify the server URL, endpoint, and credentials.",
        }


@router.get("/connections", response_model=List[MCPServerConnectionResponse])
def list_connections(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """List current user's MCP server connections."""
    rows = db.query(MCPServerConnection).filter(
        MCPServerConnection.user_id == current_user.id
    ).order_by(MCPServerConnection.created_at.desc()).all()
    return [_connection_to_response(r) for r in rows]


@router.post("/connections", response_model=MCPServerConnectionResponse, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: MCPServerConnectionCreate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Register a new MCP server connection. Credentials are stored encrypted."""
    encrypted = None
    if body.credentials:
        encrypted = encrypt_json(body.credentials)
    endpoint_path = (body.endpoint_path or "/mcp").strip()
    if not endpoint_path.startswith("/"):
        endpoint_path = "/" + endpoint_path
    conn = MCPServerConnection(
        user_id=current_user.id,
        name=body.name,
        base_url=body.base_url.strip().rstrip("/"),
        endpoint_path=endpoint_path,
        auth_type=body.auth_type or "none",
        encrypted_credentials=encrypted,
        is_platform_configured=False,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _connection_to_response(conn)


@router.get("/connections/{connection_id}", response_model=MCPServerConnectionResponse)
def get_connection(
    connection_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return _connection_to_response(conn)


@router.patch("/connections/{connection_id}", response_model=MCPServerConnectionResponse)
def update_connection(
    connection_id: int,
    body: MCPServerConnectionUpdate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if body.name is not None:
        conn.name = body.name
    if body.base_url is not None:
        conn.base_url = body.base_url.strip().rstrip("/")
    if body.endpoint_path is not None:
        ep = body.endpoint_path.strip()
        conn.endpoint_path = ep if ep.startswith("/") else "/" + ep
    if body.auth_type is not None:
        conn.auth_type = body.auth_type
    if body.credentials is not None:
        conn.encrypted_credentials = encrypt_json(body.credentials)
    if body.is_active is not None:
        conn.is_active = body.is_active
    db.commit()
    db.refresh(conn)
    return _connection_to_response(conn)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    db.delete(conn)
    db.commit()
    return None


# --- Platform tool configs (Vector DB, Postgres, File system) ---

@router.get("/tools", response_model=List[MCPToolConfigResponse])
def list_tools(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """List current user's platform MCP tool configs (no credentials in response)."""
    rows = db.query(MCPToolConfig).filter(
        MCPToolConfig.user_id == current_user.id
    ).order_by(MCPToolConfig.created_at.desc()).all()
    return [_tool_to_response(r) for r in rows]


@router.post("/tools/validate")
def validate_tool_config(
    body: ValidateToolConfigRequest,
    current_user: User = Depends(get_current_business_user),
):
    """Validate tool config (test connection) before save. Does not store anything."""
    from services.mcp_validate import validate_tool_config as do_validate
    tool_type_str = (body.tool_type or "").strip().lower()
    try:
        MCPToolType(tool_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tool_type")
    valid, message = do_validate(tool_type_str, body.config)
    return {"valid": valid, "message": message}


@router.post("/tools", response_model=MCPToolConfigResponse, status_code=status.HTTP_201_CREATED)
def create_tool(
    body: MCPToolConfigCreate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Add a platform tool config. Config (credentials) stored encrypted."""
    try:
        tool_type = MCPToolType((body.tool_type or "").strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="tool_type must be one of: vector_db, pinecone, weaviate, qdrant, chroma, postgres, mysql, elasticsearch, pageindex, filesystem, s3, slack, github, notion, rest_api",
        )
    encrypted = encrypt_json(body.config)
    business_description = (body.business_description or "").strip() or None
    if business_description and len(business_description) > 2000:
        business_description = business_description[:2000]
    tool = MCPToolConfig(
        user_id=current_user.id,
        tool_type=tool_type,
        name=body.name,
        encrypted_config=encrypted,
        business_description=business_description,
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return _tool_to_response(tool)


@router.get("/tools/{tool_id}", response_model=MCPToolConfigResponse)
def get_tool(
    tool_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    return _tool_to_response(t)


@router.patch("/tools/{tool_id}", response_model=MCPToolConfigResponse)
def update_tool(
    tool_id: int,
    body: MCPToolConfigUpdate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    if body.name is not None:
        t.name = body.name
    if body.config is not None:
        t.encrypted_config = encrypt_json(body.config)
    if body.business_description is not None:
        bd = (body.business_description or "").strip() or None
        t.business_description = bd[:2000] if bd and len(bd) > 2000 else bd
    if body.is_active is not None:
        t.is_active = body.is_active
    db.commit()
    db.refresh(t)
    return _tool_to_response(t)


@router.post("/tools/{tool_id}/refresh-schema")
def refresh_tool_schema(
    tool_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Introspect the database for this tool (Postgres/MySQL only) and store schema metadata.
    Does not overwrite existing schema on connection failure.
    """
    from services.db_schema_introspection import introspect_sql_tool
    from core.encryption import decrypt_json

    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    if t.tool_type not in (MCPToolType.POSTGRES, MCPToolType.MYSQL):
        raise HTTPException(
            status_code=400,
            detail="Schema refresh is only available for PostgreSQL and MySQL tools",
        )
    config = decrypt_json(t.encrypted_config)
    schema_dict, error = introspect_sql_tool(t.tool_type.value, config)
    if error:
        raise HTTPException(status_code=400, detail=error)
    t.schema_metadata = json.dumps(schema_dict)
    db.commit()
    db.refresh(t)
    table_count = len(schema_dict.get("tables", []))
    return {"success": True, "message": f"Schema refreshed: {table_count} table(s)", "table_count": table_count}


@router.delete("/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tool(
    tool_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    db.delete(t)
    db.commit()
    return None


# --- MCP proxy: forward JSON-RPC to user's MCP server ---

@router.post("/proxy")
async def mcp_proxy(
    body: MCPProxyRequest,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Forward a JSON-RPC request to the user's MCP server using stored credentials."""
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == body.connection_id,
        MCPServerConnection.user_id == current_user.id,
        MCPServerConnection.is_active == True,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found or inactive")
    credentials = None
    if conn.encrypted_credentials:
        credentials = decrypt_json(conn.encrypted_credentials)
    # Observability: log MCP proxy request
    log_entry = AuditLog(
        entity_type="mcp",
        entity_id=conn.id,
        action="proxy_request",
        details=json.dumps({"method": body.method, "user_id": current_user.id}),
    )
    db.add(log_entry)
    db.commit()
    from services.mcp_client import call_mcp_server
    result = await call_mcp_server(
        base_url=conn.base_url,
        endpoint_path=conn.endpoint_path or "/mcp",
        method=body.method,
        params=body.params,
        auth_type=conn.auth_type,
        credentials=credentials,
    )
    return result


# --- Invoke platform MCP tool (for UI or agent-driven invocation) ---

class InvokePlatformToolRequest(BaseModel):
    tool_name: str
    arguments: dict


@router.post("/call-platform-tool")
async def call_platform_tool(
    body: InvokePlatformToolRequest,
    current_user: User = Depends(get_current_business_user),
):
    """
    Invoke a platform MCP tool by name (e.g. platform_1_MyDB).
    Backend calls the platform MCP server with X-MCP-Business-Id so tools are scoped to the current user.
    """
    from core.config import settings
    if not settings.PLATFORM_MCP_SERVER_URL or not settings.MCP_INTERNAL_SECRET:
        raise HTTPException(status_code=503, detail="Platform MCP server not configured")
    from services.mcp_client import call_tool
    base = settings.PLATFORM_MCP_SERVER_URL.rstrip("/")
    extra_headers = {"X-MCP-Business-Id": str(current_user.id)}
    try:
        result = await call_tool(
            base_url=base,
            tool_name=body.tool_name,
            arguments=body.arguments,
            endpoint_path="/mcp",
            extra_headers=extra_headers,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# --- Tool registry (for agent orchestration: discover available MCP tools) ---

@router.get("/registry")
async def get_registry(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Return MCP tool registry for the current business user, split so the UI can show:
    - platform_tools: internal platform MCP server tools (Vector DB, Postgres, File system)
    - connection_tools: per external MCP connection, the tools exposed by that server (tools/list).
    Also returns combined "tools" for backward compatibility.
    """
    from services.mcp_client import list_tools as mcp_list_tools

    platform_tools = db.query(MCPToolConfig).filter(
        MCPToolConfig.user_id == current_user.id,
        MCPToolConfig.is_active == True,
    ).order_by(MCPToolConfig.name).all()
    connections = db.query(MCPServerConnection).filter(
        MCPServerConnection.user_id == current_user.id,
        MCPServerConnection.is_active == True,
    ).all()

    # Build platform registry entries (no credentials)
    platform_entries = []
    for t in platform_tools:
        platform_entries.append({
            "source": "platform",
            "id": t.id,
            "name": _registry_tool_name(t.id, t.name),
            "tool_type": t.tool_type.value,
            "description": _registry_description(t.tool_type.value, t.name),
        })

    # For each connection, fetch tools from that MCP server (tools/list)
    connection_tools = []
    for c in connections:
        creds = None
        if c.encrypted_credentials:
            try:
                creds = decrypt_json(c.encrypted_credentials)
            except Exception:
                creds = None
        base_url = (c.base_url or "").strip().rstrip("/")
        endpoint_path = (c.endpoint_path or "/mcp").strip()
        if not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path
        tools_list = []
        error_msg = None
        try:
            result = await mcp_list_tools(
                base_url=base_url,
                endpoint_path=endpoint_path,
                auth_type=c.auth_type or "none",
                credentials=creds,
                timeout=15.0,
            )
            for tool in (result.get("tools") or []):
                tools_list.append({
                    "name": tool.get("name") or "",
                    "description": (tool.get("description") or "")[:500],
                })
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to list tools for connection %s (%s): %s", c.name, base_url, e)
            error_msg = "Failed to list tools for this connection."
        connection_tools.append({
            "connection_id": c.id,
            "name": c.name,
            "base_url": c.base_url,
            "tools": tools_list,
            "error": error_msg,
        })

    # Combined list for backward compatibility (platform + one entry per connection)
    tools = list(platform_entries)
    for c in connections:
        tools.append({
            "source": "external",
            "connection_id": c.id,
            "name": c.name,
            "base_url": c.base_url,
        })
    return {
        "tools": tools,
        "platform_tools": platform_entries,
        "connection_tools": connection_tools,
        "platform_tool_count": len(platform_entries),
    }


def _registry_tool_name(tool_id: int, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in (name or "").strip())[:50]
    return f"platform_{tool_id}_{safe}" if safe else f"platform_{tool_id}"


def _registry_description(tool_type: str, name: str) -> str:
    d = {
        "vector_db": "Vector database",
        "pinecone": "Pinecone",
        "weaviate": "Weaviate",
        "qdrant": "Qdrant",
        "chroma": "Chroma",
        "postgres": "PostgreSQL",
        "mysql": "MySQL",
        "elasticsearch": "Elasticsearch",
        "pageindex": "PageIndex",
        "filesystem": "File system",
        "s3": "AWS S3",
        "slack": "Slack",
        "github": "GitHub",
        "notion": "Notion",
        "rest_api": "REST API",
    }
    return f"{d.get(tool_type, tool_type)}: {name}"
