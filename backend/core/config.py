import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
        DATABASE_URL: str = (
            os.getenv("DATABASE_URL")
            or os.getenv("POSTGRESQLCONNSTR_DefaultConnection")
            or os.getenv("CUSTOMCONNSTR_DefaultConnection")
            or "postgresql://postgres:postgres@localhost:5432/agent_marketplace"
        )
        SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
        ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
        PLATFORM_COMMISSION_RATE: float = float(os.getenv("PLATFORM_COMMISSION_RATE", "0.10"))
        # External API (for end users / systems outside the platform)
        EXTERNAL_API_KEY: str = os.getenv("EXTERNAL_API_KEY", "")
        EXTERNAL_TOKEN_EXPIRE_DAYS: int = int(os.getenv("EXTERNAL_TOKEN_EXPIRE_DAYS", "7"))
        EXTERNAL_API_BASE_URL: str = os.getenv("EXTERNAL_API_BASE_URL", "http://localhost:8000")
        # A2A adapter: when set, OpenAI-compatible agents are called via this A2A endpoint (platform runs A2A everywhere)
        A2A_ADAPTER_URL: str = os.getenv("A2A_ADAPTER_URL", "http://a2a-openai-adapter:8080")
        # Platform MCP server: URL for the platform-hosted MCP server (tool discovery + invocation for agents)
        PLATFORM_MCP_SERVER_URL: str = os.getenv("PLATFORM_MCP_SERVER_URL", "http://platform-mcp-server:8081")
        # Internal secret for platform MCP server and backend-to-MCP-server calls (same secret in both)
        MCP_INTERNAL_SECRET: str = os.getenv("MCP_INTERNAL_SECRET", "")
        # When True, allow agent endpoints that resolve to private/loopback IPs (dev, Docker, same host). Default False in production.
        ALLOW_PRIVATE_AGENT_ENDPOINTS: bool = os.getenv("ALLOW_PRIVATE_AGENT_ENDPOINTS", "false").lower() in ("true", "1", "yes")
        # Job document storage backend: local filesystem (default) or S3-compatible object storage (Ceph RGW)
        OBJECT_STORAGE_BACKEND: str = os.getenv("OBJECT_STORAGE_BACKEND", "local")
        S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")
        S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "")
        S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "")
        S3_BUCKET: str = os.getenv("S3_BUCKET", "")
        S3_REGION: str = os.getenv("S3_REGION", "us-east-1")
        S3_ADDRESSING_STYLE: str = os.getenv("S3_ADDRESSING_STYLE", "path")
        S3_CONNECT_TIMEOUT_SECONDS: int = int(os.getenv("S3_CONNECT_TIMEOUT_SECONDS", "5"))
        S3_READ_TIMEOUT_SECONDS: int = int(os.getenv("S3_READ_TIMEOUT_SECONDS", "60"))
        S3_MAX_POOL_CONNECTIONS: int = int(os.getenv("S3_MAX_POOL_CONNECTIONS", "100"))
        S3_RETRY_MODE: str = os.getenv("S3_RETRY_MODE", "standard")
        S3_MAX_ATTEMPTS: int = int(os.getenv("S3_MAX_ATTEMPTS", "5"))
        S3_AUTO_CREATE_BUCKET: bool = os.getenv("S3_AUTO_CREATE_BUCKET", "false").lower() in ("true", "1", "yes")
        # Signature version: s3v4 recommended for Ceph RGW compatibility
        S3_SIGNATURE_VERSION: str = os.getenv("S3_SIGNATURE_VERSION", "s3v4")
        # TCP keepalive prevents idle connections from being dropped by firewalls/load balancers
        S3_TCP_KEEPALIVE: bool = os.getenv("S3_TCP_KEEPALIVE", "true").lower() in ("true", "1", "yes")
        JOB_UPLOAD_MAX_FILE_BYTES: int = int(os.getenv("JOB_UPLOAD_MAX_FILE_BYTES", "104857600"))  # 100 MB default

        class Config:
            env_file = ".env"


settings = Settings()
