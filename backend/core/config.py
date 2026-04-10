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
    # Validate executor→agent JSON payloads (types + sandhi_trace). Set false only as a temporary escape hatch.
    EXECUTOR_PAYLOAD_VALIDATE: bool = True
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
    # MCP invocation guardrails (all MCP tool calls: platform + BYO)
    MCP_INVOCATION_MAX_ATTEMPTS: int = 3
    MCP_INVOCATION_RETRY_BASE_DELAY_SECONDS: float = 0.25
    MCP_INVOCATION_RETRY_MAX_DELAY_SECONDS: float = 3.0
    MCP_INVOCATION_RETRY_JITTER_SECONDS: float = 0.15
    MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    MCP_CIRCUIT_BREAKER_OPEN_SECONDS: float = 30.0
    MCP_CIRCUIT_BREAKER_HALF_OPEN_MAX_PROBES: int = 1
    # Optional distributed state for MCP guardrails (recommended for multi-worker production).
    MCP_GUARDRAILS_DISTRIBUTED_ENABLED: bool = False
    MCP_GUARDRAILS_REDIS_URL: str = ""
    MCP_GUARDRAILS_REDIS_PREFIX: str = "sandhi:mcp_guardrails:v1"
    MCP_GUARDRAILS_REDIS_SOCKET_TIMEOUT_SECONDS: float = 2.0
    MCP_GUARDRAILS_REDIS_CONNECT_TIMEOUT_SECONDS: float = 2.0
    MCP_GUARDRAILS_COUNTER_TTL_SECONDS: int = 120
    MCP_GUARDRAILS_BREAKER_TTL_SECONDS: int = 300
    # Set to 0 to disable; recommended to tune in production per tenant scale.
    # Concurrent admission controls. Keep >1 in production to allow expected parallel agent calls.
    MCP_TENANT_MAX_CONCURRENT_CALLS: int = 32
    MCP_TARGET_MAX_CONCURRENT_CALLS: int = 8
    # Queue wait budget before returning mcp_quota_exceeded when concurrency slots are saturated.
    MCP_CONCURRENCY_WAIT_SECONDS: float = 10.0
    MCP_TENANT_RATE_LIMIT_PER_MINUTE: int = 0
    # Retry profile split: stricter defaults for write-like operations.
    MCP_READ_INVOCATION_MAX_ATTEMPTS: int = 0
    MCP_READ_INVOCATION_RETRY_BASE_DELAY_SECONDS: float = 0.0
    MCP_READ_INVOCATION_RETRY_MAX_DELAY_SECONDS: float = 0.0
    MCP_READ_INVOCATION_RETRY_JITTER_SECONDS: float = 0.0
    MCP_WRITE_INVOCATION_MAX_ATTEMPTS: int = 0
    MCP_WRITE_INVOCATION_RETRY_BASE_DELAY_SECONDS: float = 0.0
    MCP_WRITE_INVOCATION_RETRY_MAX_DELAY_SECONDS: float = 0.0
    MCP_WRITE_INVOCATION_RETRY_JITTER_SECONDS: float = 0.0
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
    CELERY_EXECUTE_RETRY_BACKOFF_MAX_SECONDS: int = 600
    # Execution heartbeat runtime visibility.
    # Redis live-state defaults to CELERY_BROKER_URL when HEARTBEAT_REDIS_URL is empty.
    HEARTBEAT_ENABLE_REDIS: bool = True
    HEARTBEAT_REDIS_URL: str = ""
    HEARTBEAT_REDIS_TTL_SECONDS: int = 180
    HEARTBEAT_ENABLE_DB_SNAPSHOT: bool = True
    HEARTBEAT_DB_MIN_UPDATE_SECONDS: int = 45
    # Signed heartbeat ingestion contract (worker -> internal API).
    HEARTBEAT_SIGNED_API_ENABLED: bool = True
    HEARTBEAT_SIGNED_API_VERSION: str = "sandhi.heartbeat.v1"
    HEARTBEAT_SIGNED_API_SKEW_SECONDS: int = 120
    HEARTBEAT_NONCE_TTL_SECONDS: int = 300
    HEARTBEAT_RETENTION_DAYS: int = 30
    # Step-level stuck watchdog thresholds (durable DB fallback; independent from Redis liveness).
    STEP_STUCK_THRESHOLD_SECONDS: int = 600
    STEP_STUCK_BLOCKED_THRESHOLD_SECONDS: int = 900
    # Loop detection for stuck diagnosis (adapter/tool loops under heavy load).
    STEP_LOOP_ROUND_THRESHOLD: int = 10
    STEP_REPEAT_TOOLCALL_THRESHOLD: int = 6
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
    # Job scheduler: set DISABLE_SCHEDULER=true for tests/CI to prevent Celery ETA tasks from being enqueued
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
    # --- Platform Agent Planner (Issue #62): BRD analysis, workflow split, tool suggestion ---
    # When AGENT_PLANNER_API_KEY is set, planner handles those flows instead of marketplace agent endpoints.
    AGENT_PLANNER_ENABLED: bool = True
    AGENT_PLANNER_PROVIDER: str = "openai_compatible"  # openai_compatible | anthropic
    AGENT_PLANNER_BASE_URL: str = ""  # e.g. https://api.openai.com/v1 or https://api.mistral.ai/v1
    AGENT_PLANNER_API_KEY: str = ""
    AGENT_PLANNER_MODEL: str = "gpt-4o-mini"
    AGENT_PLANNER_TEMPERATURE: float = 0.3
    AGENT_PLANNER_MAX_TOKENS: int = 4096
    AGENT_PLANNER_FALLBACK_MODEL: str = ""  # optional; also uses LLM_HTTP_FALLBACK_MODEL if empty
    AGENT_PLANNER_HTTP_TIMEOUT_SECONDS: float = 120.0
    # Planner transport is chosen at runtime per job/agent mix.
    # Native A2A planner endpoint (used when runtime policy picks native_a2a).
    AGENT_PLANNER_A2A_URL: str = ""
    # Optional Bearer for platform → native A2A planner (model keys may live only on the agent service).
    AGENT_PLANNER_A2A_API_KEY: str = ""
    # Dedicated planner A2A adapter URL (used when runtime policy picks a2a_adapter). Do not reuse A2A_ADAPTER_URL.
    AGENT_PLANNER_ADAPTER_URL: str = ""
    # Secondary planner profile (failover for another upstream API key / model / base URL only).
    # Uses the same A2A hop and dedicated adapter URL as the primary.
    AGENT_PLANNER_SECONDARY_ENABLED: bool = False
    AGENT_PLANNER_SECONDARY_PROVIDER: str = ""  # empty -> inherit primary provider
    AGENT_PLANNER_SECONDARY_BASE_URL: str = ""
    AGENT_PLANNER_SECONDARY_API_KEY: str = ""
    AGENT_PLANNER_SECONDARY_MODEL: str = ""
    AGENT_PLANNER_SECONDARY_FALLBACK_MODEL: str = ""
    AGENT_PLANNER_SECONDARY_HTTP_TIMEOUT_SECONDS: float = 120.0
    # Multi-agent task split: retries when platform planner is configured (no hired-agent split fallback).
    AGENT_PLANNER_SPLIT_MAX_ATTEMPTS: int = 4
    AGENT_PLANNER_SPLIT_RETRY_BACKOFF_SECONDS: float = 2.0
    # After a failed parse, one extra planner call tries to repair output into valid JSON.
    AGENT_PLANNER_SPLIT_JSON_REPAIR: bool = True
    # Before each retry after the first, re-fetch job documents (S3/MinIO/local) for fresh BRD text.
    AGENT_PLANNER_SPLIT_RELOAD_DOCS_BETWEEN_ATTEMPTS: bool = True
    # At job execution start, re-run planner split and replace workflow steps (same agents/order/tools).
    AGENT_PLANNER_EXECUTE_REPLAN: bool = True
    # When execute-time replan fails: fail (default) or continue with the workflow built in the UI.
    AGENT_PLANNER_EXECUTE_REPLAN_ON_FAILURE: str = "fail"  # fail | continue
    # Optional read-through cache for GET planner artifact raw JSON (empty = disabled).
    PLANNER_ARTIFACT_CACHE_REDIS_URL: str = ""
    PLANNER_ARTIFACT_CACHE_TTL_SECONDS: int = 300
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
    # When True, consecutive steps with depends_on_previous=False run in parallel (separate DB sessions).
    WORKFLOW_PARALLEL_INDEPENDENT_STEPS: bool = True
    # Cap concurrent step executions per job (semaphore inside each parallel wave).
    WORKFLOW_MAX_PARALLEL_STEPS: int = 8
    # Step execution guardrails (sequential + async): hard timeout + bounded retries.
    AGENT_STEP_TIMEOUT_SECONDS: float = 180.0
    AGENT_STEP_MAX_RETRIES: int = 2
    AGENT_STEP_RETRY_BACKOFF_SECONDS: float = 2.0
    # Output quality gates before passing step output downstream.
    AGENT_OUTPUT_REQUIRE_NONEMPTY: bool = True
    AGENT_OUTPUT_MIN_CONFIDENCE: float = 0.0
    # Use a fixed seed for tool-calling agent rounds to reduce SQL/output drift across identical prompts.
    AGENT_TOOLCALL_OPENAI_SEED: int = 42
    # Developer (publish-user) KPI/SLA thresholds.
    DEVELOPER_KPI_SLA_SUCCESS_RATE_MIN: float = 0.95
    DEVELOPER_KPI_SLA_P95_LATENCY_SECONDS_MAX: float = 30.0
    # Optional webhook alerts for developer KPI SLA changes.
    DEVELOPER_KPI_ALERTS_ENABLED: bool = False
    DEVELOPER_KPI_ALERT_WEBHOOK_URL: str = ""
    DEVELOPER_KPI_ALERT_COOLDOWN_SECONDS: int = 900
    # Optional business/end-user job lifecycle webhook alerts.
    BUSINESS_JOB_ALERTS_ENABLED: bool = False
    BUSINESS_JOB_ALERT_WEBHOOK_URL: str = ""
    BUSINESS_JOB_ALERT_COOLDOWN_SECONDS: int = 180
    BUSINESS_KPI_ALERTS_ENABLED: bool = False
    BUSINESS_KPI_ALERT_WEBHOOK_URL: str = ""
    BUSINESS_KPI_ALERT_COOLDOWN_SECONDS: int = 900
    BUSINESS_KPI_SLA_SUCCESS_RATE_MIN: float = 0.95
    BUSINESS_KPI_SLA_P95_LATENCY_SECONDS_MAX: float = 45.0
    # --- Tool assignment registry + A2A task envelope ---
    # Absolute path to JSON registry; empty = packaged backend/resources/config/tool_assignment_registry.default.json
    TOOL_ASSIGNMENT_REGISTRY_PATH: str = ""
    TOOL_ASSIGNMENT_ENABLED: bool = True
    # When true, merge llm_suggested_tool_names from step input_data (planner) into assignment order.
    TOOL_ASSIGNMENT_USE_LLM: bool = True
    # When true with USE_LLM, executor asks the platform planner to pick tool names from the allowlist (no planner pre-fill).
    TOOL_ASSIGNMENT_LLM_PICK_TOOLS: bool = True
    # Max tool names the planner may return for TOOL_ASSIGNMENT_LLM_PICK_TOOLS (capped by visible tool count).
    TOOL_ASSIGNMENT_LLM_MAX_TOOLS: int = 12
    # Validate JSON size + sandhi_a2a_task before every A2A HTTP call.
    A2A_OUTBOUND_VALIDATE: bool = True
    A2A_OUTBOUND_MAX_BYTES: int = 4194304
    # When true, sandhi_a2a_task must be present and parseable (in addition to outbound checks).
    A2A_TASK_ENVELOPE_STRICT: bool = True

settings = Settings()
