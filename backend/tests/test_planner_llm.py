"""Unit tests for platform Agent Planner LLM client (Issue #62)."""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

import services.planner_llm as planner_mod
from services.planner_llm import _split_openai_messages, get_planner_public_meta, is_agent_planner_configured


@pytest.fixture(autouse=True)
def _planner_llm_tests_ignore_dotenv_transport(monkeypatch):
    """
    Unit tests that mock HTTP calls force empty adapter/native endpoints so default
    runtime behavior stays on direct provider HTTP unless a test sets runtime scope.
    """
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_A2A_URL", "")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_SECONDARY_ENABLED", False)


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
    assert _openai_chat_url("") == "https://api.openai.com/v1/chat/completions"
    assert _openai_chat_url("   ") == "https://api.openai.com/v1/chat/completions"


def test_get_planner_public_meta_shape(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "gpt-4o")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://custom/v1")
    tok = planner_mod.set_planner_runtime_transport({"transport": "direct", "reason": "test"})
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_A2A_URL", "")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "")
    meta = get_planner_public_meta()
    assert meta["provider"] == "openai_compatible"
    assert meta["model"] == "gpt-4o"
    assert meta["base_url_configured"] is True
    assert meta["transport"] == "direct"
    assert meta["native_a2a_url_configured"] is False
    assert meta["planner_adapter_url_configured"] is False

    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "")
    assert get_planner_public_meta()["base_url_configured"] is False
    planner_mod.reset_planner_runtime_transport(tok)


def test_split_openai_messages():
    system, rest = _split_openai_messages(
        [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": {"nested": 1}},
        ]
    )
    assert system == "A\n\nB"
    assert len(rest) == 2
    assert rest[0]["role"] == "user"
    assert rest[1]["role"] == "assistant"
    assert rest[1]["content"] == {"nested": 1}


def test_split_openai_messages_system_non_string_content():
    system, rest = _split_openai_messages(
        [{"role": "system", "content": {"rules": True}}, {"role": "user", "content": "ok"}]
    )
    assert "rules" in system
    assert len(rest) == 1


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


@pytest.mark.asyncio
async def test_planner_chat_completion_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(planner_mod, "is_agent_planner_configured", lambda: False)
    with pytest.raises(RuntimeError, match="not configured"):
        await planner_mod.planner_chat_completion([{"role": "user", "content": "hi"}])


def test_is_agent_planner_configured_native_a2a_without_upstream_key(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_A2A_URL", "http://planner-a2a:8080")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "")
    assert is_agent_planner_configured() is False
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "sk-test")
    assert is_agent_planner_configured() is True


def test_is_agent_planner_configured_a2a_adapter_requires_url_and_key(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    assert is_agent_planner_configured() is True

    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "http://adapter:8080")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "")
    assert is_agent_planner_configured() is False

    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    assert is_agent_planner_configured() is True


@pytest.mark.asyncio
async def test_planner_chat_completion_native_a2a(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_A2A_URL", "http://planner-a2a:8080")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "ignored-for-hop")
    tok = planner_mod.set_planner_runtime_transport({"transport": "native_a2a", "reason": "test"})
    with patch.object(planner_mod, "execute_via_a2a", new_callable=AsyncMock) as ex:
        ex.return_value = {"content": "native-out"}
        out = await planner_mod.planner_chat_completion([{"role": "user", "content": "hi"}])
    planner_mod.reset_planner_runtime_transport(tok)
    assert out == "native-out"
    ex.assert_called_once()
    assert ex.call_args[0][0] == "http://planner-a2a:8080"
    payload = ex.call_args[0][1]
    assert payload.get("schema_version") == planner_mod.PLANNER_CHAT_SCHEMA
    assert payload["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_planner_chat_completion_a2a_adapter_openai_metadata(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "http://planner-adapter:8080")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "sk-up")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "gpt-4o-mini")
    tok = planner_mod.set_planner_runtime_transport({"transport": "a2a_adapter", "reason": "test"})
    with patch.object(planner_mod, "execute_via_a2a", new_callable=AsyncMock) as ex:
        ex.return_value = {"content": "adapter-out"}
        out = await planner_mod.planner_chat_completion(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            temperature=0.2,
            max_tokens=100,
        )
    planner_mod.reset_planner_runtime_transport(tok)
    assert out == "adapter-out"
    meta = ex.call_args[1]["adapter_metadata"]
    assert meta["upstream_provider"] == "openai_compatible"
    assert meta["openai_url"].endswith("/chat/completions")
    assert meta["openai_api_key"] == "sk-up"
    assert meta["openai_temperature"] == 0.2
    assert meta["openai_max_tokens"] == 100
    assert [m["role"] for m in meta["openai_messages"]] == ["system", "user"]


@pytest.mark.asyncio
async def test_planner_chat_completion_a2a_adapter_anthropic_metadata(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "http://planner-adapter:8080")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "ak")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "anthropic")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "claude-3-opus")
    tok = planner_mod.set_planner_runtime_transport({"transport": "a2a_adapter", "reason": "test"})
    with patch.object(planner_mod, "execute_via_a2a", new_callable=AsyncMock) as ex:
        ex.return_value = {"content": "claude-via-a2a"}
        out = await planner_mod.planner_chat_completion(
            [{"role": "user", "content": "go"}],
            temperature=0.1,
            max_tokens=500,
        )
    planner_mod.reset_planner_runtime_transport(tok)
    assert out == "claude-via-a2a"
    meta = ex.call_args[1]["adapter_metadata"]
    assert meta["upstream_provider"] == "anthropic"
    assert meta["anthropic_api_key"] == "ak"
    assert meta["anthropic_model"] == "claude-3-opus"
    assert meta["anthropic_temperature"] == 0.1
    assert meta["anthropic_max_tokens"] == 500


@pytest.mark.asyncio
async def test_planner_chat_completion_switches_to_secondary_on_primary_failure(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "primary-key")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "primary-model")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://primary/v1")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_SECONDARY_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_SECONDARY_API_KEY", "secondary-key")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_SECONDARY_MODEL", "secondary-model")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_SECONDARY_BASE_URL", "https://secondary/v1")
    tok = planner_mod.set_planner_runtime_transport({"transport": "direct", "reason": "test"})
    mocked = AsyncMock(side_effect=[RuntimeError("primary down"), "secondary ok"])
    monkeypatch.setattr(planner_mod, "_openai_compatible_planner_completion", mocked)
    out = await planner_mod.planner_chat_completion([{"role": "user", "content": "x"}])
    planner_mod.reset_planner_runtime_transport(tok)
    assert out == "secondary ok"
    assert mocked.await_count == 2
    assert mocked.await_args_list[0].kwargs["api_key"] == "primary-key"
    assert mocked.await_args_list[1].kwargs["api_key"] == "secondary-key"


@pytest.mark.asyncio
async def test_planner_chat_completion_a2a_adapter_requires_api_key(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ADAPTER_URL", "http://planner-adapter:8080")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    assert is_agent_planner_configured() is False
    with pytest.raises(RuntimeError, match="not configured"):
        await planner_mod.planner_chat_completion([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_planner_chat_completion_openai_list_content_and_fallback(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "primary")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_FALLBACK_MODEL", "fallback")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.2)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 100)

    posts = {"n": 0}

    class FakeRespOk:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"text": "part"},
                                {"content": "two"},
                            ]
                        }
                    }
                ]
            }

    class FakeResp429:
        status_code = 429

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, json=None, headers=None):
            posts["n"] += 1
            if posts["n"] == 1:
                return FakeResp429()
            return FakeRespOk()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    text = await planner_mod.planner_chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=256,
    )
    assert "part" in text and "two" in text
    assert posts["n"] == 2


@pytest.mark.asyncio
async def test_planner_chat_completion_openai_numeric_content(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "m")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.2)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 50)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_FALLBACK_MODEL", "")

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": 42}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    text = await planner_mod.planner_chat_completion([{"role": "user", "content": "x"}])
    assert text == "42"


@pytest.mark.asyncio
async def test_planner_chat_completion_generic_error_logs(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "openai_compatible")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "m")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.2)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 10)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            raise ValueError("boom")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(ValueError, match="boom"):
        await planner_mod.planner_chat_completion([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_planner_chat_completion_anthropic_success(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "ak")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "anthropic")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "claude-3")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.5)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 500)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_FALLBACK_MODEL", "")

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "tool_use", "id": "1"},
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, json=None, headers=None):
            assert "anthropic.com" in url
            assert json.get("system") == "sys"
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = await planner_mod.planner_chat_completion(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
        ]
    )
    assert out == "Hello"


@pytest.mark.asyncio
async def test_planner_chat_completion_anthropic_fallback_and_roles(monkeypatch):
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_API_KEY", "ak")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_PROVIDER", "claude")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MODEL", "primary")
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_TEMPERATURE", 0.1)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_MAX_TOKENS", 100)
    monkeypatch.setattr(planner_mod.settings, "AGENT_PLANNER_FALLBACK_MODEL", "fb")

    bodies = []

    class FakeRespErr:
        status_code = 503

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class FakeRespOk:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "ok"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, json=None, headers=None):
            bodies.append(json)
            if len(bodies) == 1:
                return FakeRespErr()
            return FakeRespOk()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    text = await planner_mod.planner_chat_completion(
        [
            {"role": "user", "content": 123},
            {"role": "tool", "content": "ignored"},
        ]
    )
    assert text == "ok"
    assert bodies[1]["model"] == "fb"
    # Non user/assistant roles become user; non-str content stringified
    msgs = bodies[1]["messages"]
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "123"
    assert msgs[1]["role"] == "user" and msgs[1]["content"] == "ignored"
