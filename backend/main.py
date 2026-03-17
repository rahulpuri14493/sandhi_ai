"""
Sandhi AI API application.

This module sets up the FastAPI application, including database initialization,
error handling, and routing.
"""

import time
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import OperationalError
from db.database import engine, Base
from db.run_mcp_migration import run_initial_migrations_if_needed, run_mcp_migration_if_needed
from api.routes import (
    auth_router,
    agents_router,
    jobs_router,
    payments_router,
    dashboards_router,
    hiring_router,
    external_jobs_router,
    mcp_router,
    mcp_internal_router,
)
from middleware.error_handler import (
    validation_exception_handler,
    http_exception_handler,
    general_exception_handler,
)
from core.encryption import ensure_encryption_key_for_production
from core.logging_config import configure_logging

# Configure logging
configure_logging()

# Create database tables (retry until DB is ready, e.g. in Docker)
def _init_db() -> None:
    """
    Initialize the database by creating tables.

    This function will retry creating the tables for up to 30 seconds.
    """
    for attempt in range(30):
        try:
            # Attempt to create tables
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError:
            # If the DB is not ready, wait for 1 second and try again
            if attempt == 29:
                # If we've tried 30 times and failed, raise the error
                raise
            time.sleep(1)

# Initialize the database
_init_db()

# Run initial migrations if needed
def _run_initial_migrations() -> None:
    """
    Run initial migrations if needed.
    """
    run_initial_migrations_if_needed()

# Run MCP migration if needed
def _run_mcp_migration() -> None:
    """
    Run MCP migration if needed.
    """
    run_mcp_migration_if_needed()

# Ensure encryption key for production
def _ensure_encryption_key() -> None:
    """
    Ensure encryption key for production.
    """
    ensure_encryption_key_for_production()

# Initialize the encryption key and run migrations
_init_db()
_run_initial_migrations()
_run_mcp_migration()
_ensure_encryption_key()

# Create the FastAPI application
app = FastAPI(
    title="Sandhi AI API",
    description="API for the Sandhi AI Platform",
    version="1.0.0",
)

# CORS middleware
def _cors_config() -> CORSMiddleware:
    """
    Configure CORS middleware.

    Returns:
        CORSMiddleware: The configured CORS middleware.
    """
    return CORSMiddleware(
        allow_origins=["http://localhost:3000"],  # Next.js default port
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Add CORS middleware
app.add_middleware(_cors_config())

# Error handlers
def _add_exception_handlers() -> None:
    """
    Add exception handlers to the application.
    """
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

# Add exception handlers
_add_exception_handlers()

# Include routers
def _include_routers() -> None:
    """
    Include routers in the application.
    """
    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(jobs_router)
    app.include_router(payments_router)
    app.include_router(dashboards_router)
    app.include_router(hiring_router)
    app.include_router(external_jobs_router)
    app.include_router(mcp_router)
    app.include_router(mcp_internal_router)

# Include routers
_include_routers()

# Root endpoint
@app.get("/")
def _root() -> dict:
    """
    Root endpoint.

    Returns:
        dict: A dictionary with the API message and version.
    """
    return {"message": "Sandhi AI API", "version": "1.0.0"}

# Health check endpoint
@app.get("/health")
def _health_check() -> dict:
    """
    Health check endpoint.

    Returns:
        dict: A dictionary with the health status.
    """
    return {"status": "healthy"}