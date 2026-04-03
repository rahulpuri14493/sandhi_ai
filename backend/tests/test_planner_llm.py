"""Unit tests for platform Agent Planner LLM client (Issue #62)."""
import httpx
import pytest

import services.planner_llm as planner_mod
from services.planner_llm import is_agent_planner_configured


def test_is_agent_planner_configured_requires_api_key(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "")
    assert is_agent_planner_configured() is False

    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "sk-test")
    assert is_agent_planner_configured() is True

    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", False)
    assert is_agent_planner_configured() is False


def test_openai_chat_url_normalizes_base():
    from services.planner_llm import _openai_chat_url

    assert _openai_chat_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert _openai_chat_url("https://x/v1/chat/completions") == "https://x/v1/chat/completions"


@pytest.mark.asyncio
async def test_planner_chat_completion_openai_success(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "m")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.2)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 100)

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, json=None, headers=None):
            assert url.endswith("/chat/completions")
            assert headers.get("Authorization") == "Bearer k"
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    text = await planner_mod.planner_chat_completion(
        [{"role": "user", "content": "hi"}],
        temperature=0.1,
    )
    assert text == "hello"


@pytest.mark.asyncio
async def test_planner_chat_completion_openai_http_error_logs_and_reraises(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "m")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.2)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 100)

    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp = httpx.Response(502, request=req)

    class FakeResp:
        status_code = 502

        def raise_for_status(self):
            raise httpx.HTTPStatusError("bad gateway", request=req, response=resp)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, json=None, headers=None):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    with pytest.raises(httpx.HTTPStatusError):
        await planner_mod.planner_chat_completion([{"role": "user", "content": "hi"}])
