import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import OperationalError
from db.database import engine, Base
from db.run_mcp_migration import run_mcp_migration_if_needed
from api.routes import auth, agents, jobs, payments, dashboards, hiring, external_jobs, mcp, mcp_internal
from middleware.error_handler import (
    validation_exception_handler,
    http_exception_handler,
    general_exception_handler,
)
from core.encryption import ensure_encryption_key_for_production

# Create database tables (retry until DB is ready, e.g. in Docker)
def _init_db():
    for attempt in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError:
            if attempt == 29:
                raise
            time.sleep(1)
_init_db()
run_mcp_migration_if_needed()
ensure_encryption_key_for_production()

app = FastAPI(
    title="Sandhi AI API",
    description="API for the Sandhi AI Platform",
    version="1.0.0"
)

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
    return {"status": "healthy"}
