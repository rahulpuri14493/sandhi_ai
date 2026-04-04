"""Unit tests for services.llm_http_client (mocked httpx)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import services.llm_http_client as mod


@pytest.mark.asyncio
async def test_post_openai_success_first_attempt(monkeypatch):
    ok = MagicMock()
    ok.status_code = 200

    async def fake_post(url, headers, payload, *, timeout, verify):
        return ok

    monkeypatch.setattr(mod, "_post_once", fake_post)
    monkeypatch.setattr(mod, "httpx_verify_parameter", lambda: True)
    r = await mod.post_openai_compatible_raw("http://x/v1", {}, {"model": "m"}, max_retries=0)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_post_openai_retries_then_success(monkeypatch):
    calls = {"n": 0}
    ok = MagicMock()
    ok.status_code = 200

    async def fake_post(url, headers, payload, *, timeout, verify):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.RequestError("boom", request=MagicMock())
        return ok

    sleep_mock = AsyncMock()
    monkeypatch.setattr(mod.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(mod, "_post_once", fake_post)
    monkeypatch.setattr(mod, "httpx_verify_parameter", lambda: True)
    r = await mod.post_openai_compatible_raw("http://x/v1", {}, {"model": "m"}, max_retries=2)
    assert r.status_code == 200
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_post_openai_raises_after_retries(monkeypatch):
    async def fake_post(*a, **k):
        raise httpx.RequestError("fail", request=MagicMock())

    monkeypatch.setattr(mod, "_post_once", fake_post)
    monkeypatch.setattr(mod, "httpx_verify_parameter", lambda: True)
    with pytest.raises(httpx.RequestError):
        await mod.post_openai_compatible_raw("http://x", {}, {}, max_retries=1)


@pytest.mark.asyncio
async def test_post_openai_fallback_model_on_503(monkeypatch):
    fail = MagicMock()
    fail.status_code = 503
    ok = MagicMock()
    ok.status_code = 200
    payloads = []

    async def fake_post(url, headers, payload, *, timeout, verify):
        payloads.append(payload.get("model"))
        if payload.get("model") == "fb":
            return ok
        return fail

    monkeypatch.setattr(mod, "_post_once", fake_post)
    monkeypatch.setattr(mod, "httpx_verify_parameter", lambda: True)
    r = await mod.post_openai_compatible_raw(
        "http://x",
        {},
        {"model": "main"},
        max_retries=0,
        fallback_model="fb",
    )
    assert r.status_code == 200
    assert payloads == ["main", "fb"]


@pytest.mark.asyncio
async def test_post_openai_fallback_request_error_logged(monkeypatch, caplog):
    fail = MagicMock()
    fail.status_code = 503

    async def fake_post(url, headers, payload, *, timeout, verify):
        if payload.get("model") == "fb":
            raise httpx.RequestError("x", request=MagicMock())
        return fail

    monkeypatch.setattr(mod, "_post_once", fake_post)
    monkeypatch.setattr(mod, "httpx_verify_parameter", lambda: True)
    caplog.set_level("WARNING")
    r = await mod.post_openai_compatible_raw(
        "http://x",
        {},
        {"model": "main"},
        max_retries=0,
        fallback_model="fb",
    )
    assert r.status_code == 503
    assert any("Fallback model request failed" in rec.message for rec in caplog.records)
