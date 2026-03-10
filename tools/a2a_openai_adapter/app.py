"""
A2A ↔ OpenAI-compatible adapter.

Accepts A2A protocol (JSON-RPC 2.0 SendMessage) and forwards requests to an
OpenAI-compatible endpoint, then returns an A2A response.

Two modes:
1) Platform-driven (per-request target): Request params.metadata can contain
   openai_url, openai_api_key, openai_model. When present, the adapter uses these
   for the upstream call. Used by the Sandhi AI platform so all agent calls go
   through A2A; no env vars required.
2) Standalone (env): Set OPENAI_COMPATIBLE_URL (and optionally OPENAI_API_KEY,
   OPENAI_MODEL) for a single fixed endpoint. For local/dev use.

Configure via environment (optional when platform sends metadata):
  OPENAI_COMPATIBLE_URL  - default upstream URL (used if metadata has no openai_url)
  OPENAI_API_KEY        - default Bearer token
  OPENAI_MODEL          - default model name
  ADAPTER_PORT          - default 8080
"""
import ipaddress
import logging
import os
import socket
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

app = FastAPI(
    title="A2A ↔ OpenAI Adapter",
    description="Translates A2A SendMessage to OpenAI chat/completions and back.",
)

# Defaults (used when request metadata does not provide per-request target)
OPENAI_URL_DEFAULT = os.environ.get("OPENAI_COMPATIBLE_URL", "").strip()
OPENAI_API_KEY_DEFAULT = os.environ.get("OPENAI_API_KEY", "").strip() or None
OPENAI_MODEL_DEFAULT = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
# When set, per-request openai_url must match or be subdomain of this host (SSRF mitigation)
_default_hostname = (urlparse(OPENAI_URL_DEFAULT).hostname or "").strip().lower() if OPENAI_URL_DEFAULT else ""
JSONRPC_VERSION = "2.0"
METHOD_SEND_MESSAGE = "SendMessage"
ROLE_AGENT = "ROLE_AGENT"


def _extract_text_from_parts(parts: List[Dict[str, Any]]) -> str:
    if not parts:
        return ""
    texts = []
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            texts.append(str(p["text"]))
    return "\n".join(texts)


def _openai_content_to_text(content: Any) -> str:
    """OpenAI can return content as string or list of parts (e.g. multimodal)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p)
            for p in content
        )
    return str(content) if content is not None else ""


@app.get("/health")
def health():
    return {"status": "ok", "openai_configured": bool(OPENAI_URL_DEFAULT)}


def _validate_openai_url(url: str) -> str:
    """
    SSRF mitigation: http/https only; when OPENAI_COMPATIBLE_URL is set, hostname must
    match or be a subdomain of it; resolved IP must not be private/loopback/link-local.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Missing OpenAI URL.")

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid OpenAI URL.")

    if parsed.scheme not in ("http", "https"):
        raise ValueError("OpenAI URL must use http or https.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid OpenAI URL hostname.")

    # When a default upstream is configured, allow only that host or its subdomains
    if _default_hostname:
        h = hostname.lower()
        if h != _default_hostname and not (h.endswith("." + _default_hostname)):
            raise ValueError("OpenAI URL hostname is not allowed")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError("Unable to resolve OpenAI URL hostname.")

    for family, _, _, _, sockaddr in addrinfo:
        ip_str = None
        if family == socket.AF_INET:
            ip_str = sockaddr[0]
        elif family == socket.AF_INET6:
            ip_str = sockaddr[0]
        if ip_str is None:
            continue
        ip_obj = ipaddress.ip_address(ip_str)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
        ):
            raise ValueError("OpenAI URL points to a disallowed IP address.")

    return url


def _resolve_target(metadata: Dict[str, Any]) -> tuple:
    """Return (url, api_key, model) for upstream OpenAI call. Per-request metadata overrides env."""
    url = (metadata.get("openai_url") or "").strip() or OPENAI_URL_DEFAULT
    key = (metadata.get("openai_api_key") or "").strip() or OPENAI_API_KEY_DEFAULT
    model = (metadata.get("openai_model") or metadata.get("model") or "").strip() or OPENAI_MODEL_DEFAULT
    return url, key, model


@app.post("/")
async def a2a_endpoint(request: Request):
    """
    A2A JSON-RPC endpoint. Accepts SendMessage and returns a direct Message
    response. Uses params.metadata.openai_url / openai_api_key / openai_model
    when provided (platform mode); otherwise uses env OPENAI_COMPATIBLE_URL etc.
    """
    try:
        body = await request.json()
    except Exception:
        logger.exception("A2A adapter: parse error reading request body")
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
        )

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if method != METHOD_SEND_MESSAGE:
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not supported: {method}. This adapter only supports SendMessage.",
                },
            },
        )

    message = params.get("message") or {}
    parts = message.get("parts") or []
    user_text = _extract_text_from_parts(parts)
    if not user_text:
        user_text = "(empty message)"

    metadata = params.get("metadata") or {}
    openai_url, openai_api_key, openai_model = _resolve_target(metadata)
    openai_messages = metadata.get("openai_messages")

    if not openai_url:
        return JSONResponse(
            status_code=503,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": "Adapter not configured: set OPENAI_COMPATIBLE_URL or pass openai_url in metadata",
                },
            },
        )

    try:
        validated_openai_url = _validate_openai_url(openai_url)
    except ValueError:
        logger.warning("Invalid openai_url provided", exc_info=True)
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": "Invalid openai_url",
                },
            },
        )

    headers = {"Content-Type": "application/json"}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    elif openai_api_key:
        headers["Authorization"] = f"Bearer {openai_api_key}"

    # Use pre-formatted messages from platform when present so the model gets
    # system + user structure and returns the actual answer instead of echoing context.
    if isinstance(openai_messages, list) and len(openai_messages) > 0:
        messages = openai_messages
    else:
        messages = [{"role": "user", "content": user_text}]

    payload = {
        "model": openai_model,
        "messages": messages,
        "temperature": 0.7,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # codeql[py/full-ssrf] URL validated by _validate_openai_url (scheme, hostname/domain, public IP)
            response = await client.post(validated_openai_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Upstream API error {e.response.status_code}: {e.response.text[:500]}",
                },
            },
        )
    except Exception:
        logger.exception("Upstream request failed for request id %s", req_id)
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": "Upstream request failed",
                },
            },
        )

    # Parse OpenAI-style response
    choices = data.get("choices") or []
    if not choices:
        content = data.get("error", {}).get("message", "No choices in response") or "No choices in response"
    else:
        msg = choices[0].get("message") or {}
        content = _openai_content_to_text(msg.get("content"))

    # A2A direct message response (same shape the platform's a2a_client expects)
    result = {
        "message": {
            "role": ROLE_AGENT,
            "parts": [{"text": content}],
        }
    }

    return JSONResponse(
        status_code=200,
        content={
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "result": result,
        },
    )


# Alias for clarity: some clients may POST to /a2a or /send-message
@app.post("/a2a")
async def a2a_alias(request: Request):
    return await a2a_endpoint(request)
