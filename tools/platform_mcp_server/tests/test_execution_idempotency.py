"""Tests for Redis + in-memory idempotency cache."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import execution_idempotency as ei


@pytest.fixture(autouse=True)
def _reset_idempotency_module():
    ei._redis_client = None
    ei._redis_cooldown_until = 0.0
    ei._STORE.clear()
    yield
    ei._redis_client = None
    ei._redis_cooldown_until = 0.0
    ei._STORE.clear()


def test_no_key_runs_factory_every_time():
    calls = []

    def f():
        calls.append(1)
        return json.dumps({"status": "ok"})

    assert ei.cached_tool_json("scope", "", f) == json.dumps({"status": "ok"})
    assert ei.cached_tool_json("scope", "  ", f) == json.dumps({"status": "ok"})
    assert len(calls) == 2


def test_memory_dedupes_success():
    calls = []

    def f():
        calls.append(1)
        return json.dumps({"status": "ok"})

    a = ei.cached_tool_json("s", "idem-mem-1", f, cache_success_only=True)
    b = ei.cached_tool_json("s", "idem-mem-1", f, cache_success_only=True)
    assert a == b
    assert len(calls) == 1


def test_memory_does_not_cache_failed_json_when_success_only():
    calls = []

    def f():
        calls.append(1)
        return json.dumps({"error": "nope"})

    ei.cached_tool_json("s", "idem-fail", f, cache_success_only=True)
    ei.cached_tool_json("s", "idem-fail", f, cache_success_only=True)
    assert len(calls) == 2


def test_redis_short_circuit_before_factory(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    fake = MagicMock()
    fake.ping.return_value = True
    fake.get.return_value = json.dumps({"status": "ok", "from": "redis"})
    fake.setex = MagicMock()
    with patch("redis.Redis.from_url", return_value=fake):
        calls = []

        def f():
            calls.append(1)
            raise RuntimeError("factory should not run")

        out = ei.cached_tool_json("scope", "idem-redis-hit", f, cache_success_only=True)
    assert json.loads(out).get("from") == "redis"
    assert calls == []


def test_redis_setex_on_success(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    fake = MagicMock()
    fake.ping.return_value = True
    fake.get.return_value = None
    fake.setex = MagicMock()
    with patch("redis.Redis.from_url", return_value=fake):

        def f():
            return json.dumps({"status": "ok"})

        ei.cached_tool_json("scope", "idem-redis-set", f, cache_success_only=True)
    assert fake.setex.called
    args, _kw = fake.setex.call_args
    assert args[0].startswith("sandhi:platform_mcp:idemp:v1:")
    assert args[2] == json.dumps({"status": "ok"})
