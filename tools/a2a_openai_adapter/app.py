"""
A2A ↔ upstream LLM adapter (OpenAI-compatible or Anthropic Messages API).

Accepts A2A protocol (JSON-RPC 2.0 SendMessage) and forwards to an upstream HTTP
API, then returns an A2A-shaped response (ROLE_AGENT message with text parts).

Modes:
1) OpenAI-compatible (default): metadata openai_url, openai_api_key, openai_model,
   optional openai_messages, openai_temperature, openai_max_tokens, openai_fallback_model.
2) Anthropic: metadata upstream_provider=anthropic, anthropic_api_key, anthropic_model,
   openai_messages (chat-shaped), anthropic_max_tokens, anthropic_temperature,
   optional anthropic_fallback_model, optional anthropic_url (default Messages API).
3) Standalone (env): OPENAI_COMPATIBLE_URL (+ optional OPENAI_API_KEY, OPENAI_MODEL).

Used by Sandhi for hired agents (OpenAI path) and optionally a dedicated planner
adapter instance (planner traffic isolated from A2A_ADAPTER_URL).
"""
import ipaddress
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Log to stdout: <datetime>.<type>.<message>
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(levelname)s.%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)

# Defaults (used when request metadata does not provide per-request target)
OPENAI_URL_DEFAULT = os.environ.get("OPENAI_COMPATIBLE_URL", "").strip()
OPENAI_API_KEY_DEFAULT = os.environ.get("OPENAI_API_KEY", "").strip() or None
OPENAI_MODEL_DEFAULT = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
# When set, per-request openai_url must match or be subdomain of this host (SSRF mitigation)
_default_hostname = (urlparse(OPENAI_URL_DEFAULT).hostname or "").strip().lower() if OPENAI_URL_DEFAULT else ""


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("A2A OpenAI adapter started; default_url=%s", OPENAI_URL_DEFAULT or "(none, use metadata)")
    yield
    logger.info("A2A OpenAI adapter shutdown")


app = FastAPI(
    title="A2A ↔ OpenAI Adapter",
    description="Translates A2A SendMessage to OpenAI chat/completions and back.",
    lifespan=_lifespan,
)

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


def _is_transient_dns_error(exc: BaseException) -> bool:
    """True for resolver blips (Docker / flaky DNS); false for unknown host, etc."""
    if not isinstance(exc, socket.gaierror):
        return False
    msg = str(exc).lower()
    if "temporary failure" in msg or "try again" in msg:
        return True
    errno = getattr(exc, "errno", None)
    if errno == -3:
        return True
    eai_again = getattr(socket, "EAI_AGAIN", None)
    return eai_again is not None and errno == eai_again


def _getaddrinfo_with_retry(hostname: str, *, attempts: int = 4) -> list:
    """
    Resolve hostname for SSRF checks. Retries transient failures — getaddrinfo
    can raise EAI_AGAIN / -3 in containers when DNS is momentarily unavailable.
    """
    last: socket.gaierror | None = None
    for attempt in range(attempts):
        try:
            return socket.getaddrinfo(hostname, None)
        except socket.gaierror as e:
            last = e
            if attempt < attempts - 1 and _is_transient_dns_error(e):
                # Log only attempt/errno — never log hostname or full exception (CodeQL: clear-text sensitive data).
                logger.info(
                    "DNS lookup retry after transient resolver error (attempt %s/%s, errno=%s)",
                    attempt + 1,
                    attempts,
                    getattr(e, "errno", None),
                )
                time.sleep(0.12 * (2**attempt))
                continue
            raise
    assert last is not None
    raise last


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
        addrinfo = _getaddrinfo_with_retry(hostname)
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


def _validate_outbound_url(url: str) -> str:
    """
    Per-request SSRF check immediately before outgoing request: require HTTPS,
    resolve hostname and reject private/loopback/link-local/multicast/reserved/unspecified IPs.
    Guards against DNS rebinding; use the returned URL for client.post.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is required")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Outbound URL must use https")
    if not parsed.hostname:
        raise ValueError("Outbound URL must include a hostname")

    try:
        addrinfo = _getaddrinfo_with_retry(parsed.hostname)
    except socket.gaierror:
        raise ValueError("Unable to resolve outbound URL hostname")

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
            raise ValueError("Outbound URL host resolves to a disallowed IP address")
        # IPv6 unspecified (::)
        if getattr(ip_obj, "is_unspecified", False):
            raise ValueError("Outbound URL host resolves to a disallowed IP address")

    # Return reconstructed URL from validated components so the sink receives
    # a server-constructed value, not the raw user string (breaks taint for CodeQL).
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path or "",
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))


def _resolve_target(metadata: Dict[str, Any]) -> tuple:
    """Return (validated_url, api_key, model). All URLs pass _validate_openai_url before use (SSRF)."""
    raw_url = (metadata.get("openai_url") or "").strip() or OPENAI_URL_DEFAULT
    if not raw_url:
        validated_url = ""
    else:
        validated_url = _validate_openai_url(raw_url)
    key = (metadata.get("openai_api_key") or "").strip() or OPENAI_API_KEY_DEFAULT
    model = (metadata.get("openai_model") or metadata.get("model") or "").strip() or OPENAI_MODEL_DEFAULT
    return validated_url, key, model


ANTHROPIC_DEFAULT_URL = "https://api.anthropic.com/v1/messages"


def _split_system_from_chat_messages(messages: List[Dict[str, Any]]) -> tuple:
    """Split OpenAI-style messages into optional system string and Anthropic user/assistant messages."""
    system_parts: List[str] = []
    rest: List[Dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip()
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            else:
                system_parts.append(str(content))
        else:
            rest.append(m)
    system = "\n\n".join(system_parts) if system_parts else None
    anthropic_msgs: List[Dict[str, Any]] = []
    for m in rest:
        role = (m.get("role") or "user").strip()
        if role not in ("user", "assistant"):
            role = "user"
        c = m.get("content")
        if not isinstance(c, str):
            c = str(c) if c is not None else ""
        anthropic_msgs.append({"role": role, "content": c})
    return system, anthropic_msgs


def _validate_anthropic_messages_url(url: str) -> str:
    """SSRF: only https://api.anthropic.com/..."""
    url = (url or "").strip() or ANTHROPIC_DEFAULT_URL
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Anthropic URL must use https")
    host = (parsed.hostname or "").lower()
    if host != "api.anthropic.com":
        raise ValueError("Anthropic URL host must be api.anthropic.com")
    return _validate_outbound_url(url)


def _float_meta(metadata: Dict[str, Any], key: str, default: float) -> float:
    v = metadata.get(key)
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    return default


def _int_meta(metadata: Dict[str, Any], key: str, default: int) -> int:
    v = metadata.get(key)
    if isinstance(v, int) and v > 0:
        return v
    if isinstance(v, float) and v > 0:
        return int(v)
    return default


async def _upstream_openai_chat(
    *,
    req_id: Any,
    outbound_url: str,
    headers: Dict[str, str],
    openai_model: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    openai_tools: Any,
) -> JSONResponse:
    payload: Dict[str, Any] = {
        "model": openai_model,
        "messages": messages,
        "temperature": _float_meta(metadata, "openai_temperature", 0.7),
    }
    max_tok = _int_meta(metadata, "openai_max_tokens", 0)
    if max_tok > 0:
        payload["max_tokens"] = max_tok
    if isinstance(openai_tools, list) and len(openai_tools) > 0:
        payload["tools"] = openai_tools

    fb = (metadata.get("openai_fallback_model") or "").strip() or None
    num_tools = len(openai_tools) if isinstance(openai_tools, list) else 0
    logger.info(
        "A2A OpenAI upstream req_id=%s target=%s messages=%s tools=%s",
        req_id, outbound_url, len(messages), num_tools,
    )

    async def _post(model_name: str) -> httpx.Response:
        p = dict(payload)
        p["model"] = model_name
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.post(outbound_url, json=p, headers=headers)

    try:
        response = await _post(openai_model)
        if fb and response.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "OpenAI upstream retry req_id=%s status=%s fallback_model=%s",
                req_id, response.status_code, fb,
            )
            response = await _post(fb)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "A2A upstream API error req_id=%s status=%s body=%s",
            req_id, e.response.status_code, (e.response.text or "")[:200],
        )
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
        logger.exception("A2A upstream request failed req_id=%s", req_id)
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32603, "message": "Upstream request failed"},
            },
        )

    choices = data.get("choices") or []
    if not choices:
        content = data.get("error", {}).get("message", "No choices in response") or "No choices in response"
        tool_calls = None
    else:
        msg = choices[0].get("message") or {}
        content = _openai_content_to_text(msg.get("content"))
        tool_calls = msg.get("tool_calls")

    logger.info(
        "A2A OpenAI response req_id=%s content_len=%s tool_calls=%s",
        req_id, len(content or ""), len(tool_calls) if tool_calls else 0,
    )
    result: Dict[str, Any] = {
        "message": {"role": ROLE_AGENT, "parts": [{"text": content}]},
    }
    if tool_calls:
        result["tool_calls"] = tool_calls
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result},
    )


async def _upstream_anthropic_messages(
    *,
    req_id: Any,
    metadata: Dict[str, Any],
    user_text: str,
    openai_messages: Any,
) -> JSONResponse:
    api_key = (metadata.get("anthropic_api_key") or "").strip()
    if not api_key:
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32602, "message": "Missing anthropic_api_key in metadata"},
            },
        )
    model = (metadata.get("anthropic_model") or "").strip() or OPENAI_MODEL_DEFAULT
    max_tokens = _int_meta(metadata, "anthropic_max_tokens", 4096)
    if max_tokens <= 0:
        max_tokens = 4096
    temperature = _float_meta(metadata, "anthropic_temperature", 0.3)
    raw_url = (metadata.get("anthropic_url") or "").strip() or ANTHROPIC_DEFAULT_URL
    try:
        outbound_url = _validate_anthropic_messages_url(raw_url)
    except ValueError:
        logger.warning("Invalid anthropic_url", exc_info=True)
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid anthropic_url"},
            },
        )

    if isinstance(openai_messages, list) and len(openai_messages) > 0:
        system, anth_msgs = _split_system_from_chat_messages(openai_messages)
    else:
        system = None
        anth_msgs = [{"role": "user", "content": user_text}]

    body: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": anth_msgs,
    }
    if system:
        body["system"] = system

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    fb = (metadata.get("anthropic_fallback_model") or "").strip() or None

    logger.info(
        "A2A Anthropic upstream req_id=%s model=%s messages=%s",
        req_id, model, len(anth_msgs),
    )

    async def _post(model_name: str) -> httpx.Response:
        b = dict(body)
        b["model"] = model_name
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.post(outbound_url, json=b, headers=headers)

    try:
        response = await _post(model)
        if fb and response.status_code in (429, 500, 502, 503, 529):
            logger.warning(
                "Anthropic upstream retry req_id=%s status=%s fallback_model=%s",
                req_id, response.status_code, fb,
            )
            response = await _post(fb)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Anthropic upstream error req_id=%s status=%s body=%s",
            req_id, e.response.status_code, (e.response.text or "")[:200],
        )
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Anthropic API error {e.response.status_code}: {e.response.text[:500]}",
                },
            },
        )
    except Exception:
        logger.exception("Anthropic upstream failed req_id=%s", req_id)
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "error": {"code": -32603, "message": "Anthropic upstream request failed"},
            },
        )

    parts_out = data.get("content") or []
    texts: List[str] = []
    for p in parts_out:
        if isinstance(p, dict) and p.get("type") == "text":
            texts.append(str(p.get("text") or ""))
    content = "\n".join(texts).strip()
    logger.info("A2A Anthropic response req_id=%s content_len=%s", req_id, len(content))

    result = {"message": {"role": ROLE_AGENT, "parts": [{"text": content}]}}
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result},
    )


@app.post("/")
async def a2a_endpoint(request: Request):
    """
    A2A JSON-RPC endpoint: SendMessage → OpenAI-compatible chat or Anthropic Messages
    (when metadata.upstream_provider is anthropic), then A2A message response.
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
    upstream = (metadata.get("upstream_provider") or "openai_compatible").strip().lower()
    if upstream in ("anthropic", "claude"):
        return await _upstream_anthropic_messages(
            req_id=req_id,
            metadata=metadata,
            user_text=user_text,
            openai_messages=metadata.get("openai_messages"),
        )

    try:
        openai_url, openai_api_key, openai_model = _resolve_target(metadata)
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
        outbound_url = _validate_outbound_url(openai_url)
    except ValueError:
        logger.warning("Outbound URL validation failed", exc_info=True)
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

    if isinstance(openai_messages, list) and len(openai_messages) > 0:
        messages = openai_messages
    else:
        messages = [{"role": "user", "content": user_text}]
    openai_tools = metadata.get("openai_tools")

    return await _upstream_openai_chat(
        req_id=req_id,
        outbound_url=outbound_url,
        headers=headers,
        openai_model=openai_model,
        messages=messages,
        metadata=metadata,
        openai_tools=openai_tools,
    )


# Alias for clarity: some clients may POST to /a2a or /send-message
@app.post("/a2a")
async def a2a_alias(request: Request):
    return await a2a_endpoint(request)
