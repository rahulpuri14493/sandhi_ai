"""
A2A (Agent-to-Agent) protocol client.
Uses JSON-RPC 2.0 over HTTP to call SendMessage and optionally GetTask.
See: https://a2a-protocol.org/latest/specification/
"""
from typing import Dict, Any, List, Optional
import ipaddress
import json
import socket
import uuid
from urllib.parse import urlparse

import httpx


# JSON-RPC 2.0 and A2A constants
JSONRPC_VERSION = "2.0"
METHOD_SEND_MESSAGE = "SendMessage"
METHOD_GET_TASK = "GetTask"
ROLE_USER = "ROLE_USER"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_FAILED = "TASK_STATE_FAILED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"
TASK_STATE_REJECTED = "TASK_STATE_REJECTED"


def _text_part(text: str) -> Dict[str, Any]:
    """Build a Part with text content (camelCase per A2A spec)."""
    return {"text": text}


def _message_parts_from_input(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert our executor input_data into A2A Message parts.
    We send a single text part containing JSON so the agent receives full context.
    Agents that expect natural language can parse or we could add a human-readable part.
    """
    return [_text_part(json.dumps(input_data, default=str))]


def _extract_text_from_parts(parts: List[Dict[str, Any]]) -> str:
    """Extract concatenated text from A2A parts."""
    if not parts:
        return ""
    texts = []
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            texts.append(str(p["text"]))
    return "\n".join(texts)


def _extract_result_from_send_message_response(response_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse SendMessage response (result = SendMessageResponse).
    Response has either 'task' or 'message'. Extract agent output as our standard shape.
    Returns dict with 'content' key for compatibility with executor/output handling.
    """
    result = response_body.get("result")
    if not result:
        error = response_body.get("error", {})
        code = error.get("code", -32603)
        msg = error.get("message", "Unknown JSON-RPC error")
        data = error.get("data", {})
        raise Exception(f"A2A error {code}: {msg} | {data}")

    # Direct message response
    if "message" in result:
        msg = result["message"]
        parts = msg.get("parts") or []
        content = _extract_text_from_parts(parts)
        return {"content": content, "raw_message": msg}

    # Task response
    task = result.get("task")
    if not task:
        raise Exception("A2A SendMessageResponse had neither task nor message")

    status = task.get("status") or {}
    state = status.get("state", "")

    if state == TASK_STATE_COMPLETED:
        artifacts = task.get("artifacts") or []
        if artifacts:
            first_artifact = artifacts[0]
            parts = first_artifact.get("parts") or []
            content = _extract_text_from_parts(parts)
            return {"content": content, "artifacts": artifacts, "task_id": task.get("id")}
        # No artifacts: use task status message if any
        status_msg = status.get("message")
        if status_msg and isinstance(status_msg, dict):
            parts = status_msg.get("parts") or []
            content = _extract_text_from_parts(parts)
            if content:
                return {"content": content, "task_id": task.get("id")}
        return {"content": "", "task_id": task.get("id")}

    if state in (TASK_STATE_FAILED, TASK_STATE_CANCELED, TASK_STATE_REJECTED):
        msg = status.get("message")
        if isinstance(msg, dict):
            parts = msg.get("parts") or []
            detail = _extract_text_from_parts(parts) or state
        else:
            detail = str(msg) if msg else state
        raise Exception(f"A2A task ended with state {state}: {detail}")

    # Non-terminal state (e.g. INPUT_REQUIRED, AUTH_REQUIRED) - for blocking we normally wait
    # If we get here with blocking=true, server may not support blocking; return what we have
    artifacts = task.get("artifacts") or []
    if artifacts:
        first_artifact = artifacts[0]
        parts = first_artifact.get("parts") or []
        content = _extract_text_from_parts(parts)
        return {"content": content, "artifacts": artifacts, "task_id": task.get("id"), "state": state}
    return {"content": "", "task_id": task.get("id"), "state": state}


def _validate_public_http_url(url: str) -> str:
    """
    Basic SSRF protection: ensure the URL uses http/https and resolves to a public IP.
    Raises ValueError if the URL is invalid or points to a private/internal address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https schemes are allowed for agent endpoints")
    if not parsed.hostname:
        raise ValueError("Agent endpoint must include a hostname")

    try:
        addr_info = socket.getaddrinfo(parsed.hostname, None)
    except OSError as exc:
        raise ValueError(f"Could not resolve agent endpoint host: {parsed.hostname}") from exc

    for family, _, _, _, sockaddr in addr_info:
        ip_str = None
        if family == socket.AF_INET:
            ip_str = sockaddr[0]
        elif family == socket.AF_INET6:
            ip_str = sockaddr[0]
        if not ip_str:
            continue
        ip_obj = ipaddress.ip_address(ip_str)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
        ):
            raise ValueError("Agent endpoint host must resolve to a public IP address")

    return url


async def send_message(
    url: str,
    message_parts: List[Dict[str, Any]],
    *,
    api_key: Optional[str] = None,
    blocking: bool = True,
    message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """
    Send an A2A SendMessage request (JSON-RPC 2.0) to the agent endpoint.
    Returns a dict with at least 'content' (text from agent response).
    """
    message_id = message_id or str(uuid.uuid4())
    params = {
        "message": {
            "role": ROLE_USER,
            "parts": message_parts,
            "messageId": message_id,
        },
        "configuration": {"blocking": blocking},
    }
    if metadata:
        params["metadata"] = metadata

    payload = {
        "jsonrpc": JSONRPC_VERSION,
        "id": 1,
        "method": METHOD_SEND_MESSAGE,
        "params": params,
    }

    headers = {"Content-Type": "application/json"}
    if api_key and (api_key or "").strip():
        headers["Authorization"] = f"Bearer {(api_key or '').strip()}"

    normalized_url = (url or "").strip()
    safe_url = _validate_public_http_url(normalized_url)

    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        response = await client.post(safe_url, json=payload, headers=headers)

    try:
        response_body = response.json()
    except Exception as e:
        raise Exception(f"A2A response not JSON: {response.text[:500]}") from e

    if response.status_code >= 400:
        err_msg = response_body.get("error", {}).get("message", response.text[:500])
        raise Exception(f"A2A request failed {response.status_code}: {err_msg}")

    return _extract_result_from_send_message_response(response_body)


async def execute_via_a2a(
    url: str,
    input_data: Dict[str, Any],
    *,
    api_key: Optional[str] = None,
    blocking: bool = True,
    timeout: float = 120.0,
    adapter_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convenience: build message parts from input_data and send via A2A.
    Returns dict with 'content' and any extra keys from the response.
    When calling the platform's OpenAI adapter, pass adapter_metadata with
    openai_url, openai_api_key, openai_model so the adapter forwards to the right endpoint.
    """
    parts = _message_parts_from_input(input_data)
    metadata = {"source": "sandhi_ai_platform"}
    if adapter_metadata:
        metadata.update(adapter_metadata)
    return await send_message(
        url,
        parts,
        api_key=api_key,
        blocking=blocking,
        metadata=metadata,
        timeout=timeout,
    )
