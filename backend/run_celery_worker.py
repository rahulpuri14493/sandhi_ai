#!/usr/bin/env python3
"""
Start the Celery worker. When CELERY_AUTORELOAD is true (default in docker-compose),
restart the worker on .py changes under the watch root (bind-mounted /app).
"""
from __future__ import annotations

import os
import shlex
import sys


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _celery_args() -> tuple[str, ...]:
    conc = os.environ.get("CELERY_WORKER_CONCURRENCY", "4")
    mx = os.environ.get("CELERY_WORKER_AUTOSCALE_MAX", "16")
    mn = os.environ.get("CELERY_WORKER_AUTOSCALE_MIN", "2")
    return (
        "-A",
        "services.task_queue.celery_app",
        "worker",
        "--loglevel=info",
        f"--concurrency={conc}",
        f"--autoscale={mx},{mn}",
    )


def main() -> int:
    argv = ("celery",) + _celery_args()
    autoreload = _env_bool("CELERY_AUTORELOAD", False)

    if not autoreload:
        os.execvp(argv[0], list(argv))

    try:
        from watchfiles import run_process
    except ImportError:
        print(
            "watchfiles is required for CELERY_AUTORELOAD (install uvicorn[standard] or watchfiles). "
            "Starting worker without autoreload.",
            file=sys.stderr,
        )
        os.execvp(argv[0], list(argv))

    watch_root = os.environ.get("CELERY_WATCH_ROOT", "/app").strip() or "/app"
    # watchfiles ignores `args` when target is a shell command — only `target` is split and run.
    # Pass one quoted argv string so we actually start `celery worker ...`, not bare `celery` (help only).
    cmd = shlex.join(("celery",) + _celery_args())
    return int(
        run_process(
            watch_root,
            target=cmd,
            target_type="command",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
