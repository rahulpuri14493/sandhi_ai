from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


# --- MCP Server Connection ---

class MCPServerConnectionCreate(BaseModel):
    name: str
    base_url: str  # HTTP(S) URL of MCP server
    endpoint_path: str = "/mcp"  # JSON-RPC endpoint path, e.g. /mcp, /message, /
    auth_type: str = "none"  # none, bearer, api_key, basic
    credentials: Optional[dict] = None  # { "token": "..." } or { "api_key": "..." } or { "username": "...", "password": "..." }


class MCPServerConnectionUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    endpoint_path: Optional[str] = None
    auth_type: Optional[str] = None
    credentials: Optional[dict] = None
    is_active: Optional[bool] = None


class MCPServerConnectionResponse(BaseModel):
    id: int
    user_id: int
    name: str
    base_url: str
    endpoint_path: str
    auth_type: str
    is_platform_configured: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- MCP Tool Config (Platform tools: Vector DB, Postgres, File system) ---

class MCPToolConfigCreate(BaseModel):
    tool_type: str  # vector_db, postgres, filesystem
    name: str
    config: dict  # Tool-specific; will be encrypted. e.g. { "connection_string": "...", "api_key": "..." }


class MCPToolConfigUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class ValidateToolConfigRequest(BaseModel):
    tool_type: str
    config: dict


class MCPToolConfigResponse(BaseModel):
    id: int
    user_id: int
    tool_type: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- MCP API proxy (JSON-RPC forward) ---

class MCPProxyRequest(BaseModel):
    connection_id: int
    method: str
    params: Optional[dict] = None
