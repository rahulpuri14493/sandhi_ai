from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    DATABASE_URL: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/agent_marketplace",
        validation_alias=AliasChoices(
            "DATABASE_URL",
            "POSTGRESQLCONNSTR_DefaultConnection",
            "CUSTOMCONNSTR_DefaultConnection",
        ),
    )
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    PLATFORM_COMMISSION_RATE: float = 0.10
    # External API (for end users / systems outside the platform)
    EXTERNAL_API_KEY: str = ""
    EXTERNAL_TOKEN_EXPIRE_DAYS: int = 7
    EXTERNAL_API_BASE_URL: str = "http://localhost:8000"
    # A2A adapter: when set, OpenAI-compatible agents are called via this A2A endpoint (platform runs A2A everywhere)
    A2A_ADAPTER_URL: str = "http://a2a-openai-adapter:8080"
    # Platform MCP server: URL for the platform-hosted MCP server (tool discovery + invocation for agents)
    PLATFORM_MCP_SERVER_URL: str = "http://platform-mcp-server:8081"
    # Internal secret for platform MCP server and backend-to-MCP-server calls (same secret in both)
    MCP_INTERNAL_SECRET: str = ""
    # Optional dedicated encryption key for MCP credentials at rest
    MCP_ENCRYPTION_KEY: str = ""
    # When True, allow agent endpoints that resolve to private/loopback IPs (dev, Docker, same host). Default False in production.
    ALLOW_PRIVATE_AGENT_ENDPOINTS: bool = False
    # Job document storage backend: S3-compatible object storage (default) or local filesystem
    OBJECT_STORAGE_BACKEND: str = "s3"
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    S3_ADDRESSING_STYLE: str = "path"
    S3_CONNECT_TIMEOUT_SECONDS: int = 5
    S3_READ_TIMEOUT_SECONDS: int = 60
    S3_MAX_POOL_CONNECTIONS: int = 100
    S3_RETRY_MODE: str = "standard"
    S3_MAX_ATTEMPTS: int = 5
    S3_AUTO_CREATE_BUCKET: bool = False
    # Signature version: s3v4 recommended for broad S3-compatible compatibility
    S3_SIGNATURE_VERSION: str = "s3v4"
    # TCP keepalive prevents idle connections from being dropped by firewalls/load balancers
    S3_TCP_KEEPALIVE: bool = True
    # App-level retries for S3 operations (in addition to botocore retry strategy)
    S3_OPERATION_RETRY_ATTEMPTS: int = 4
    S3_OPERATION_RETRY_BASE_DELAY_SECONDS: float = 0.2
    S3_OPERATION_RETRY_MAX_DELAY_SECONDS: float = 2.0
    S3_OPERATION_RETRY_JITTER_SECONDS: float = 0.1
    JOB_UPLOAD_MAX_FILE_BYTES: int = 104857600  # 100 MB default

settings = Settings()
