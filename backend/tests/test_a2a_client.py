"""Unit tests for A2A client."""
import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.a2a_client as a2a_client_mod
from services.a2a_client import (
    _a2a_urls_equivalent,
    _extract_result_from_send_message_response,
    _extract_text_from_parts,
    _message_parts_from_input,
    _text_part,
    _validate_public_http_url,
    send_message,
    execute_via_a2a,
)


def test_text_part():
    assert _text_part("hello") == {"text": "hello"}


def test_a2a_urls_equivalent_normalizes_scheme_host_port():
    assert _a2a_urls_equivalent("http://foo:8080/", "HTTP://foo:8080")
    assert not _a2a_urls_equivalent("http://foo:8080", "http://foo:8081")
    assert not _a2a_urls_equivalent("http://foo:8080", "https://foo:8080")


def test_message_parts_from_input():
    data = {"job_title": "Test", "documents": []}
    parts = _message_parts_from_input(data)
    assert len(parts) == 1
    assert "text" in parts[0]
    parsed = json.loads(parts[0]["text"])
    assert parsed["job_title"] == "Test"


def test_extract_result_from_message():
    body = {
        "result": {
            "message": {
                "role": "ROLE_AGENT",
                "parts": [{"text": "Hello from agent"}],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "Hello from agent"


def test_extract_result_from_task_completed():
    body = {
        "result": {
            "task": {
                "id": "task-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [
                    {"artifactId": "a1", "parts": [{"text": "Result text"}]}
                ],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "Result text"
    assert out.get("task_id") == "task-1"


def test_extract_result_from_task_failed_raises():
    body = {
        "result": {
            "task": {
                "id": "task-1",
                "status": {
                    "state": "TASK_STATE_FAILED",
                    "message": {"parts": [{"text": "Something broke"}]},
                },
            }
        }
    }
    with pytest.raises(Exception) as exc_info:
        _extract_result_from_send_message_response(body)
    assert "TASK_STATE_FAILED" in str(exc_info.value)


def test_extract_result_from_jsonrpc_error_raises():
    body = {"error": {"code": -32001, "message": "Task not found"}}
    with pytest.raises(Exception) as exc_info:
        _extract_result_from_send_message_response(body)
    assert "Task not found" in str(exc_info.value) or "32001" in str(exc_info.value)


def test_extract_text_from_parts_empty_and_skips_non_text():
    assert _extract_text_from_parts([]) == ""
    assert _extract_text_from_parts([{"foo": "bar"}, {"text": "a"}, {"text": "b"}]) == "a\nb"


def test_extract_result_message_includes_tool_calls():
    body = {
        "result": {
            "message": {"parts": [{"text": "x"}]},
            "tool_calls": [{"id": "1"}],
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "x"
    assert out.get("tool_calls") == [{"id": "1"}]


def test_extract_result_task_completed_uses_status_message_when_no_artifacts():
    body = {
        "result": {
            "task": {
                "id": "t2",
                "status": {
                    "state": "TASK_STATE_COMPLETED",
                    "message": {"parts": [{"text": "from status"}]},
                },
                "artifacts": [],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "from status"
    assert out.get("task_id") == "t2"


def test_extract_result_task_completed_empty_when_no_artifacts_or_status_text():
    body = {
        "result": {
            "task": {
                "id": "t3",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == ""
    assert out.get("task_id") == "t3"


def test_extract_result_non_terminal_with_artifacts():
    body = {
        "result": {
            "task": {
                "id": "t4",
                "status": {"state": "TASK_STATE_WORKING"},
                "artifacts": [{"parts": [{"text": "partial"}]}],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "partial"
    assert out.get("state") == "TASK_STATE_WORKING"


def test_extract_result_non_terminal_no_artifacts():
    body = {
        "result": {
            "task": {
                "id": "t5",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                "artifacts": [],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == ""
    assert out.get("state") == "TASK_STATE_INPUT_REQUIRED"


def test_extract_result_task_failed_non_dict_message():
    body = {
        "result": {
            "task": {
                "id": "t6",
                "status": {"state": "TASK_STATE_FAILED", "message": "plain"},
            }
        }
    }
    with pytest.raises(Exception) as e:
        _extract_result_from_send_message_response(body)
    assert "TASK_STATE_FAILED" in str(e.value)


def test_validate_public_http_url_requires_scheme_and_hostname():
    with pytest.raises(ValueError, match="required"):
        _validate_public_http_url("")
    with pytest.raises(ValueError, match="http"):
        _validate_public_http_url("ftp://example.com")


def test_validate_public_http_url_blocks_sensitive_port(monkeypatch):
    monkeypatch.setattr(
        "services.a2a_client.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, None, None, None, ("8.8.8.8", 0))],
    )
    with pytest.raises(ValueError, match="port"):
        _validate_public_http_url("http://203.0.113.1:3306/")


def test_validate_public_http_url_accepts_public_resolve():
    """Force a public-looking resolved IP so the test does not depend on real DNS."""
    fake_addr = (socket.AF_INET, None, None, None, ("8.8.4.4", 0))
    with patch.object(
        a2a_client_mod.socket,
        "getaddrinfo",
        return_value=[fake_addr],
    ):
        u = _validate_public_http_url("https://example.com:8443/path")
    assert "example.com" in u or "8443" in u


@pytest.mark.asyncio
async def test_send_message_calls_httpx():
    # Mock URL validation so agent.example.com doesn't need to resolve (CI has no DNS for it)
    with patch(
        "services.a2a_client._validate_public_http_url",
        side_effect=lambda u, *, allow_private_resolve=False: u,
    ), patch(
        "services.a2a_client.httpx.AsyncClient"
    ) as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {"message": {"parts": [{"text": "OK"}]}}
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        result = await send_message(
            "https://agent.example.com",
            [{"text": "Hi"}],
            api_key="secret",
        )
        assert result["content"] == "OK"


@pytest.mark.asyncio
async def test_send_message_allows_private_resolve_when_target_is_configured_adapter(monkeypatch):
    """Docker/internal adapter host resolves to a private IP; must not be blocked as SSRF."""
    from core import config

    monkeypatch.setattr(config.settings, "A2A_ADAPTER_URL", "http://a2a-openai-adapter:8080")
    monkeypatch.setattr(config.settings, "ALLOW_PRIVATE_AGENT_ENDPOINTS", False)
    monkeypatch.setattr(
        "services.a2a_client.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, None, None, None, ("172.18.0.5", 0))],
    )
    with patch("services.a2a_client.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {"message": {"parts": [{"text": "adapter-ok"}]}}
        }
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        result = await send_message(
            "http://a2a-openai-adapter:8080",
            [{"text": "ping"}],
        )
    assert result["content"] == "adapter-ok"


@pytest.mark.asyncio
async def test_execute_via_a2a_builds_parts_and_calls_send():
    with patch("services.a2a_client.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"content": "Done"}
        result = await execute_via_a2a(
            "https://a2a.example.com",
            {"job_title": "J", "documents": []},
            api_key=None,
        )
        assert result["content"] == "Done"
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "https://a2a.example.com"
        assert len(call_args[0][1]) == 1
        assert "text" in call_args[0][1][0]
