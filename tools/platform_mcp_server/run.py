#!/usr/bin/env python3
"""Run the platform MCP HTTP app (uvicorn). UVICORN_RELOAD is read from the environment."""
import os

import uvicorn


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8081")),
        reload=_env_bool("UVICORN_RELOAD", False),
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
