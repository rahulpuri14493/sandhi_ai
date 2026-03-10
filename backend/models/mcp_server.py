"""
MCP (Model Context Protocol) models: user/tenant-scoped server connections
and platform-configured tools (Vector DB, PostgreSQL, File system, etc.).
Credentials are stored encrypted in the database.
"""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from db.database import Base


class MCPServerConnection(Base):
    """User's connection to an existing MCP server (external or platform)."""
    __tablename__ = "mcp_server_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(2048), nullable=False)  # e.g. https://mcp.example.com
    endpoint_path = Column(String(255), nullable=False, default="/mcp")  # e.g. /mcp, /message, /
    # auth: none, bearer, api_key, basic
    auth_type = Column(String(32), nullable=False, default="none")
    # Encrypted JSON: { "token": "..." } or { "api_key": "..." } or { "username": "...", "password": "..." }
    encrypted_credentials = Column(Text, nullable=True)
    is_platform_configured = Column(Boolean, default=False, nullable=False)  # True = use platform tools
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
    # Storage
    FILESYSTEM = "filesystem"
    S3 = "s3"
    # Integrations
    SLACK = "slack"
    GITHUB = "github"
    NOTION = "notion"
    REST_API = "rest_api"


class MCPToolConfig(Base):
    """Platform-configured MCP tools per user (Vector DB, Postgres, File system). Credentials encrypted."""
    __tablename__ = "mcp_tool_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_type = Column(Enum(MCPToolType), nullable=False)
    name = Column(String(255), nullable=False)  # e.g. "My Pinecone", "Prod DB"
    # Encrypted JSON: tool-specific config (connection strings, API keys, paths)
    encrypted_config = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="mcp_tool_configs")
