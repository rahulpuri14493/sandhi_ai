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
    # MCP tool-call guards (stability under high load)
    MCP_TOOL_MAX_ARGUMENT_BYTES: int = 5242880  # 5 MB
    MCP_TOOL_DEFAULT_TIMEOUT_SECONDS: float = 60.0
    MCP_TOOL_MAX_TIMEOUT_SECONDS: float = 300.0
    # Async MCP write operation retry policy
    MCP_WRITE_OPERATION_MAX_ATTEMPTS: int = 3
    MCP_WRITE_OPERATION_RETRY_BASE_DELAY_SECONDS: float = 0.5
    MCP_WRITE_OPERATION_RETRY_MAX_DELAY_SECONDS: float = 5.0
    MCP_WRITE_OPERATION_RETRY_JITTER_SECONDS: float = 0.2
    # Job execution backend: celery (Redis queue) or local_thread fallback.
    JOB_EXECUTION_BACKEND: str = "celery"
    JOB_EXECUTION_STRICT_QUEUE: bool = False  # True: no local fallback when enqueue fails
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    CELERY_WORKER_AUTOSCALE_MAX: int = 16
    CELERY_WORKER_AUTOSCALE_MIN: int = 2
    CELERY_WORKER_CONCURRENCY: int = 4
    CELERY_EXECUTE_MAX_RETRIES: int = 3
    CELERY_EXECUTE_RETRY_BACKOFF_SECONDS: int = 5
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
    # Job scheduler: set DISABLE_SCHEDULER=true for tests/CI to prevent APScheduler from starting
    DISABLE_SCHEDULER: bool = False
    # Stuck job watchdog: jobs in IN_PROGRESS/IN_QUEUE longer than this are flagged
    STUCK_JOB_THRESHOLD_HOURS: int = 6
    # Retry settings for ZIP extraction/transient read failures during upload.
    ZIP_EXTRACT_RETRY_ATTEMPTS: int = 3
    ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS: float = 0.1
    ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS: float = 0.5
    ZIP_EXTRACT_RETRY_JITTER_SECONDS: float = 0.05
    # httpx (LLM splitter, A2A, document analyzer): verify TLS by default; set false only for dev
    HTTPX_VERIFY_SSL: bool = True
    HTTPX_CA_BUNDLE_PATH: str = ""  # optional path to CA bundle (corporate proxy / custom roots)
    # When set, task/tool splitters retry once with this model after 429 or 5xx from the LLM endpoint
    LLM_HTTP_FALLBACK_MODEL: str = ""
    # In-process rate limiting (per client IP + route group). Tune via env for your deployment.
    # Many users behind one NAT share one IP — use higher values in production or Redis-backed limits at scale.
    RATE_LIMIT_ENABLED: bool = True
    # POST /api/auth/login, /api/auth/register (per IP; brute-force still mitigated at app layer)
    RATE_LIMIT_AUTH_PER_MINUTE: int = 300
    # GET /api/agents* — dashboards, marketplace, polling (per IP)
    RATE_LIMIT_AGENT_READS_PER_MINUTE: int = 1200
    # POST|PUT|PATCH|DELETE /api/jobs* (per IP)
    RATE_LIMIT_JOB_MUTATIONS_PER_MINUTE: int = 600
    # When True, use leftmost X-Forwarded-For for the client IP (trusted reverse proxy only).
    # Default False: ignore X-Forwarded-For so clients cannot spoof IPs and bypass limits.
    RATE_LIMIT_TRUST_PROXY_HEADERS: bool = False

settings = Settings()
