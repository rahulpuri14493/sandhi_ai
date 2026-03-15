import logging
import time
import uuid
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from db.run_alembic_upgrade import run_alembic_upgrade
from api.routes import auth, agents, jobs, payments, dashboards, hiring, external_jobs, mcp, mcp_internal
from middleware.error_handler import (
    validation_exception_handler,
    http_exception_handler,
    general_exception_handler,
)
from core.encryption import ensure_encryption_key_for_production
from core.logging_config import configure_logging
from services.job_file_storage import verify_s3_connectivity
from core.config import settings
from services.job_scheduler import JobSchedulerService

configure_logging()
logger = logging.getLogger(__name__)
request_logger = logging.getLogger("uvicorn.error")

# Run Alembic migrations (PostgreSQL only; retry until DB is ready, e.g. Docker)
for attempt in range(30):
    try:
        run_alembic_upgrade()
        break
    except Exception as e:
        if attempt == 29:
            logger.exception("Alembic upgrade failed after 30 attempts")
            raise
        logger.warning("Alembic upgrade attempt %s failed: %s; retrying in 1s", attempt + 1, e)
        time.sleep(1)
ensure_encryption_key_for_production()

# Verify S3 connectivity on startup when object storage is enabled.
# Retries alongside the Alembic loop above may already have given enough
# time for the RGW container to become healthy.  We try once more here
# and log a clear warning rather than crashing, because the RGW may still
# be starting up (the Compose healthcheck guards the depends_on gate, but
# direct deploys might not have that).
_s3_check = verify_s3_connectivity()
if _s3_check["ok"]:
    logger.info("S3 storage check passed: %s", _s3_check["detail"])
else:
    logger.warning("S3 storage check FAILED: %s — file uploads will fail until resolved", _s3_check["detail"])
_scheduler_service = JobSchedulerService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.DISABLE_SCHEDULER:
        _scheduler_service.start()
    yield
    _scheduler_service.stop()


app = FastAPI(
    title="Sandhi AI API",
    description="API for the Sandhi AI Platform",
    version="1.0.0",
    lifespan=lifespan,
)

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            request_logger.exception(
                "request.failed request_id=%s method=%s path=%s elapsed_ms=%.2f",
                request_id,
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        status_code = getattr(response, "status_code", None)
        request_logger.info(
            'API %s %s -> %s (%.2fms) request_id=%s',
            request.method,
            request.url.path,
            status_code,
            elapsed_ms,
            request_id,
        )
        # Also emit an access-log-like line directly to stdout (Docker-friendly).
        try:
            client = request.client
            client_part = f"{client.host}:{client.port}" if client else "-"
            http_version = request.scope.get("http_version", "1.1")
            # Match uvicorn's access log style.
            print(
                f'INFO:     {client_part} - "{request.method} {request.url.path} HTTP/{http_version}" {status_code}',
                flush=True,
            )
        except Exception:
            pass

        # Optional: log response preview for JSON responses (debug only; avoids dumping secrets by default).
        if os.getenv("LOG_API_RESPONSE_BODY", "").strip() in ("1", "true", "yes", "on"):
            try:
                content_type = (response.headers.get("content-type") or "").lower()
                if "application/json" in content_type and isinstance(response, Response):
                    body = getattr(response, "body", None)
                    if body:
                        preview = body.decode("utf-8", errors="replace")
                        if len(preview) > 4000:
                            preview = preview[:4000] + "…(truncated)"
                        request_logger.info("API response request_id=%s preview=%s", request_id, preview)
            except Exception:
                # Never block the request on logging.
                pass

        response.headers["x-request-id"] = request_id
        return response

# Log every API request/response (helps debugging in Docker).
app.add_middleware(RequestLoggingMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Error handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(jobs.router)
app.include_router(payments.router)
app.include_router(dashboards.router)
app.include_router(hiring.router)
app.include_router(external_jobs.router)
app.include_router(mcp.router)
app.include_router(mcp_internal.router)


@app.get("/")
def root():
    return {"message": "Sandhi AI API", "version": "1.0.0"}


@app.get("/health")
def health_check():
    s3 = verify_s3_connectivity()
    return {
        "status": "healthy" if s3["ok"] else "degraded",
        "storage": s3,
    }
