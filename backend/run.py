#!/usr/bin/env python3
"""
Simple script to run the FastAPI application
"""
import os
import uvicorn


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=_env_bool("UVICORN_RELOAD", False),
        log_level="info",
        access_log=True,
    )
