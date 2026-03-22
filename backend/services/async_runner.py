"""Run coroutines from synchronous code without asyncio.run() when a loop is already running."""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_coroutine_sync(coro: Coroutine[Any, Any, T]) -> T:
    """
    Run an async coroutine from sync code.

    Uses asyncio.run() when no event loop is running (typical Celery/thread worker).
    If a loop is already running (e.g. nested async context), runs the coroutine in a
    dedicated thread with its own asyncio.run() to avoid nested loop errors.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
