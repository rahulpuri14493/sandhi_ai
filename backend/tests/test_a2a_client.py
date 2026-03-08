"""Unit tests for A2A client."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from services.a2a_client import (
    _extract_result_from_send_message_response,
    _message_parts_from_input,
    _text_part,
    send_message,
    execute_via_a2a,
)


def test_text_part():
    assert _text_part("hello") == {"text": "hello"}


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


@pytest.mark.asyncio
async def test_send_message_calls_httpx():
    with patch("services.a2a_client.httpx.AsyncClient") as mock_client:
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
