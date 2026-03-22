from pydantic import BaseModel, Field
from typing import Optional, Any, Literal
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
    business_description: Optional[str] = None  # Optional short context for the agent (e.g. "Sales DB: orders, customers")


class MCPToolConfigUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    business_description: Optional[str] = None
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
    business_description: Optional[str] = None
    schema_metadata: Optional[str] = None  # JSON string; for list view prefer schema_table_count
    schema_table_count: Optional[int] = None  # Number of tables when schema_metadata is set (for UI)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- MCP API proxy (JSON-RPC forward) ---

class MCPProxyRequest(BaseModel):
    connection_id: int
    method: str
    params: Optional[dict] = None


# --- Platform tool invocation contracts (read/write, artifact-first) ---

class MCPArtifactRef(BaseModel):
    storage: Literal["s3", "minio", "ceph", "gcs", "azure_blob"] = "s3"
    path: str = Field(..., min_length=1)
    format: Literal["parquet", "jsonl", "csv", "json"] = "parquet"
    checksum: Optional[str] = None


class MCPTargetRef(BaseModel):
    target_type: Literal[
        "bigquery",
        "databricks",
        "snowflake",
        "sqlserver",
        "s3",
        "minio",
        "ceph",
        "aws_s3",
        "azure_blob",
        "gcs",
    ]
    name: str = Field(..., min_length=1)
    database: Optional[str] = None
    schema_name: Optional[str] = Field(default=None, alias="schema")
    table: Optional[str] = None
    bucket: Optional[str] = None
    prefix: Optional[str] = None


class MCPPlatformToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict
    timeout_seconds: Optional[float] = None


class MCPPlatformWriteRequest(BaseModel):
    tool_name: str
    artifact_ref: MCPArtifactRef
    target: MCPTargetRef
    operation_type: Literal["insert", "update", "upsert", "merge"] = "upsert"
    write_mode: Literal["append", "overwrite", "upsert", "merge"] = "upsert"
    merge_keys: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(..., min_length=8)
    options: dict = Field(default_factory=dict)
    timeout_seconds: Optional[float] = None


class MCPToolWriteResult(BaseModel):
    status: Literal["success", "failure", "accepted"]
    rows_received: Optional[int] = None
    rows_inserted: Optional[int] = None
    rows_updated: Optional[int] = None
    rows_failed: Optional[int] = None
    target: Optional[str] = None
    idempotency_key: Optional[str] = None
    operation_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class MCPWriteOperationResponse(BaseModel):
    operation_id: str
    idempotency_key: str
    tool_name: str
    status: Literal["accepted", "in_progress", "success", "failure"]
    result: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
