"""
MCP (Model Context Protocol) models: user/tenant-scoped server connections
and platform-configured tools (Vector DB, PostgreSQL, File system, etc.).
Credentials are stored encrypted in the database.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator
from datetime import datetime
import enum

from db.database import Base


class MCPServerConnection(Base):
    """User's connection to an existing MCP server (external or platform)."""

    __tablename__ = "mcp_server_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    base_url = Column(String(2048), nullable=False)  # e.g. https://mcp.example.com
    endpoint_path = Column(
        String(255), nullable=False, default="/mcp"
    )  # e.g. /mcp, /message, /
    # auth: none, bearer, api_key, basic
    auth_type = Column(String(32), nullable=False, default="none")
    # Encrypted JSON: { "token": "..." } or { "api_key": "..." } or { "username": "...", "password": "..." }
    encrypted_credentials = Column(Text, nullable=True)
    is_platform_configured = Column(
        Boolean, default=False, nullable=False
    )  # True = use platform tools
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", backref="mcp_connections")


class MCPToolType(str, enum.Enum):
    # Vector stores
    VECTOR_DB = "vector_db"
    PINECONE = "pinecone"
    WEAVIATE = "weaviate"
    QDRANT = "qdrant"
    CHROMA = "chroma"
    # Databases
    POSTGRES = "postgres"
    MYSQL = "mysql"
    # Search
    ELASTICSEARCH = "elasticsearch"
    PAGEINDEX = "pageindex"  # Vectorless RAG (keyword/tree retrieval, no vectors)
    # Storage
    FILESYSTEM = "filesystem"
    S3 = "s3"
    # Integrations
    SLACK = "slack"
    GITHUB = "github"
    NOTION = "notion"
    REST_API = "rest_api"


class _MCPToolTypeColumn(TypeDecorator):
    """Store/load MCPToolType as lowercase to match PostgreSQL mcptooltype; accept any case on read."""

    impl = String(50)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        # Use String so result processing is ours; DB column stays mcptooltype and driver returns raw string.
        return dialect.type_descriptor(String(50))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, MCPToolType):
            return value.value
        return (value or "").strip().lower() or None

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        s = (value if isinstance(value, str) else str(value)).strip().lower()
        if not s:
            return None
        return MCPToolType(s)


class MCPToolConfig(Base):
    """Platform-configured MCP tools per user (Vector DB, Postgres, File system). Credentials encrypted."""

    __tablename__ = "mcp_tool_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_type = Column(_MCPToolTypeColumn, nullable=False)
    name = Column(String(255), nullable=False)  # e.g. "My Pinecone", "Prod DB"
    # Encrypted JSON: tool-specific config (connection strings, API keys, paths)
    encrypted_config = Column(Text, nullable=False)
    # Read-only schema from introspection (tables, columns, PK, FK); no credentials
    schema_metadata = Column(Text, nullable=True)
    # Optional short business context for the agent (e.g. "Sales DB: orders, customers")
    business_description = Column(String(2000), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="mcp_tool_configs")
