"""Unit tests for services.async_runner."""

import asyncio

import pytest

from services.async_runner import run_coroutine_sync


def test_run_coroutine_sync_no_running_loop():
    async def work():
        return 7

    assert run_coroutine_sync(work()) == 7


@pytest.mark.asyncio
async def test_run_coroutine_sync_with_running_loop_uses_thread_pool():
    async def inner():
        async def work():
            return 42

        return run_coroutine_sync(work())

    assert await inner() == 42
