"""
Platform Agent Planner LLM client (Issue #62).

Unified completion API for admin-configured providers. API keys come from settings
(env / secret manager); never log key values.

Transport modes (runtime-selected, per job/tenant/agent mix):
- direct: httpx to OpenAI-compatible or Anthropic HTTP APIs.
- native_a2a: JSON-RPC A2A SendMessage to AGENT_PLANNER_A2A_URL only (planner speaks A2A).
- a2a_adapter: SendMessage to AGENT_PLANNER_ADAPTER_URL with per-request upstream metadata
  (dedicated adapter service; isolates planner traffic from A2A_ADAPTER_URL hired-agent pool).
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx

from core.config import settings
from models.agent import Agent
from services.a2a_client import execute_via_a2a
from services.httpx_tls import httpx_verify_parameter

logger = logging.getLogger(__name__)

# --- Transport constants (settings use lowercase strings) ---
PLANNER_TRANSPORT_DIRECT = "direct"
PLANNER_TRANSPORT_NATIVE_A2A = "native_a2a"
PLANNER_TRANSPORT_A2A_ADAPTER = "a2a_adapter"

PLANNER_CHAT_SCHEMA = "sandhi.planner_chat.v1"
PLANNER_TRANSPORT_AUTO = "auto"

_planner_runtime_transport_ctx: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "planner_runtime_transport_ctx", default=None
)


def _normalize_planner_transport(raw: Optional[str]) -> str:
    t = (raw or "direct").strip().lower()
    if t in ("native_a2a", "native-a2a", "a2a_native", "nativea2a"):
        return PLANNER_TRANSPORT_NATIVE_A2A
    if t in (
        "a2a_adapter",
        "a2a-adapter",
        "adapter",
        "planner_adapter",
        "via_adapter",
    ):
        return PLANNER_TRANSPORT_A2A_ADAPTER
    return PLANNER_TRANSPORT_DIRECT


def is_agent_planner_configured() -> bool:
    """True when platform planner should handle BRD analysis, split, and tool suggestion."""
    if not getattr(settings, "AGENT_PLANNER_ENABLED", True):
        return False
    primary_ready = bool((getattr(settings, "AGENT_PLANNER_API_KEY", None) or "").strip())
    secondary_ready = bool(
        getattr(settings, "AGENT_PLANNER_SECONDARY_ENABLED", False)
        and (getattr(settings, "AGENT_PLANNER_SECONDARY_API_KEY", None) or "").strip()
    )
    # Runtime transport is selected per-request/per-job from agent mix; no global env transport fallback.
    return bool(primary_ready or secondary_ready)


def get_planner_public_meta() -> Dict[str, Any]:
    """Safe to return to authenticated clients (no secrets)."""
    runtime = _planner_runtime_transport_ctx.get() or {}
    transport = (runtime.get("transport") or PLANNER_TRANSPORT_AUTO).strip().lower()
    return {
        "provider": (getattr(settings, "AGENT_PLANNER_PROVIDER", None) or "openai_compatible").strip(),
        "model": (getattr(settings, "AGENT_PLANNER_MODEL", None) or "").strip() or "gpt-4o-mini",
        "base_url_configured": bool((getattr(settings, "AGENT_PLANNER_BASE_URL", None) or "").strip()),
        "transport": transport,
        "native_a2a_url_configured": bool((getattr(settings, "AGENT_PLANNER_A2A_URL", None) or "").strip()),
        "planner_adapter_url_configured": bool(
            (getattr(settings, "AGENT_PLANNER_ADAPTER_URL", None) or "").strip()
        ),
        "secondary_configured": bool(
            getattr(settings, "AGENT_PLANNER_SECONDARY_ENABLED", False)
            and (getattr(settings, "AGENT_PLANNER_SECONDARY_API_KEY", None) or "").strip()
        ),
    }


def resolve_runtime_planner_transport(
    db,
    *,
    agent_ids: Optional[List[int]] = None,
    requested_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Decide planner transport at runtime for the current tenant/job.
    Precedence:
      1) requested_mode when explicit (direct/native_a2a/a2a_adapter)
      2) auto decision from hired agent capabilities (a2a_enabled)
      3) direct when no agents are provided
    """
    req = _normalize_planner_transport(requested_mode)
    if req in (
        PLANNER_TRANSPORT_DIRECT,
        PLANNER_TRANSPORT_NATIVE_A2A,
        PLANNER_TRANSPORT_A2A_ADAPTER,
    ):
        return {"transport": req, "reason": "explicit_override"}

    normalized_ids = [int(x) for x in (agent_ids or []) if x is not None]
    if not normalized_ids:
        return {"transport": PLANNER_TRANSPORT_DIRECT, "reason": "auto_no_agents"}

    rows = (
        db.query(Agent.id, Agent.a2a_enabled)
        .filter(Agent.id.in_(normalized_ids))
        .all()
    )
    a2a_by_id = {int(aid): bool(a2a) for aid, a2a in rows}
    selected_flags = [a2a_by_id.get(aid, False) for aid in normalized_ids]
    any_a2a = any(selected_flags)
    all_a2a = all(selected_flags) if selected_flags else False
    if any_a2a:
        return {
            "transport": PLANNER_TRANSPORT_A2A_ADAPTER,
            "reason": "auto_agents_include_a2a" if not all_a2a else "auto_all_agents_a2a",
        }
    return {"transport": PLANNER_TRANSPORT_DIRECT, "reason": "auto_agents_direct"}


@contextmanager
def planner_runtime_transport_scope(decision: Dict[str, Any]):
    token = _planner_runtime_transport_ctx.set(
        {
            "transport": (decision.get("transport") or PLANNER_TRANSPORT_DIRECT).strip().lower(),
            "reason": (decision.get("reason") or "runtime").strip() or "runtime",
        }
    )
    try:
        yield
    finally:
        _planner_runtime_transport_ctx.reset(token)


def set_planner_runtime_transport(decision: Dict[str, Any]):
    """Set runtime planner transport for current context. Returns token for reset."""
    return _planner_runtime_transport_ctx.set(
        {
            "transport": (decision.get("transport") or PLANNER_TRANSPORT_DIRECT).strip().lower(),
            "reason": (decision.get("reason") or "runtime").strip() or "runtime",
        }
    )


def reset_planner_runtime_transport(token) -> None:
    _planner_runtime_transport_ctx.reset(token)


def _nonnull_str(value: Any) -> str:
    return str(value or "").strip()


def _openai_chat_url(base: str) -> str:
    b = (base or "").strip().rstrip("/")
    if not b:
        b = "https://api.openai.com/v1"
    if b.endswith("/chat/completions"):
        return b
    return f"{b}/chat/completions"


def _split_openai_messages(
    messages: List[Dict[str, Any]],
) -> tuple[Optional[str], List[Dict[str, Any]]]:
    system_parts: List[str] = []
    rest: List[Dict[str, Any]] = []
    for m in messages:
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
    return system, rest


def _serialize_messages_for_metadata(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure messages are JSON-serializable for adapter metadata."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        c = m.get("content")
        if isinstance(c, str):
            content: Any = c
        elif c is None:
            content = ""
        else:
            content = json.dumps(c) if isinstance(c, (dict, list)) else str(c)
        out.append({"role": role, "content": content})
    return out


async def _openai_compatible_planner_completion(
    messages: List[Dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    max_tok: int,
    temp: float,
    fb_model: Optional[str],
    verify: Any,
    timeout: float,
) -> str:
    url = _openai_chat_url(base_url)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temp,
        "max_tokens": max_tok,
    }

    async def _post(mname: str) -> httpx.Response:
        p = dict(payload)
        p["model"] = mname
        async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
            return await client.post(url, json=p, headers=headers)

    resp = await _post(model)
    if fb_model and resp.status_code in (429, 500, 502, 503, 504):
        logger.warning("Planner LLM returned %s; retrying with fallback model", resp.status_code)
        resp = await _post(fb_model)
    resp.raise_for_status()
    data = resp.json()
    raw = (data.get("choices") or [{}])[0].get("message") or {}
    content = raw.get("content")
    if isinstance(content, list):
        return " ".join(
            p.get("text", p.get("content", "")) if isinstance(p, dict) else str(p) for p in content
        ).strip()
    if isinstance(content, str):
        return content.strip()
    return str(content or "").strip()


async def _anthropic_messages(
    messages: List[Dict[str, Any]],
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float,
    verify: Any,
    timeout: float,
    fallback_model: Optional[str],
) -> str:
    system, rest = _split_openai_messages(messages)
    anthropic_messages: List[Dict[str, Any]] = []
    for m in rest:
        role = m.get("role") or "user"
        if role not in ("user", "assistant"):
            role = "user"
        content = m.get("content")
        if not isinstance(content, str):
            content = str(content)
        anthropic_messages.append({"role": role, "content": content})

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": anthropic_messages,
    }
    if system:
        body["system"] = system

    async def _post(mname: str) -> httpx.Response:
        b = dict(body)
        b["model"] = mname
        async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
            return await client.post(url, json=b, headers=headers)

    resp = await _post(model)
    if fallback_model and resp.status_code in (429, 500, 502, 503, 504):
        logger.warning("Anthropic planner returned %s; retrying with fallback model", resp.status_code)
        resp = await _post(fallback_model)
    resp.raise_for_status()
    data = resp.json()
    parts = data.get("content") or []
    texts: List[str] = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            texts.append(str(p.get("text") or ""))
    return "\n".join(texts).strip()


def _planner_native_a2a_payload(
    messages: List[Dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    return {
        "schema_version": PLANNER_CHAT_SCHEMA,
        "messages": _serialize_messages_for_metadata(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


async def _planner_chat_completion_native_a2a(
    messages: List[Dict[str, Any]],
    *,
    a2a_url: str,
    hop_key: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    url = (a2a_url or "").strip()
    if not url:
        raise RuntimeError("AGENT_PLANNER_A2A_URL is required for native_a2a transport")
    input_data = _planner_native_a2a_payload(messages, temperature=temperature, max_tokens=max_tokens)
    result = await execute_via_a2a(
        url,
        input_data,
        api_key=hop_key,
        blocking=True,
        timeout=timeout,
        adapter_metadata=None,
    )
    text = (result.get("content") or "").strip()
    return text


async def _planner_chat_completion_via_dedicated_adapter(
    messages: List[Dict[str, Any]],
    *,
    adapter_url: str,
    adapter_hop_key: Optional[str],
    base_url: str,
    provider: str,
    api_key: str,
    model: str,
    max_tok: int,
    temp: float,
    fb_model: Optional[str],
    timeout: float,
) -> str:
    adapter_url = (adapter_url or "").strip()
    if not adapter_url:
        raise RuntimeError("AGENT_PLANNER_ADAPTER_URL is required for a2a_adapter transport")
    serialized = _serialize_messages_for_metadata(messages)
    hop_key = (adapter_hop_key or "").strip() or None

    if provider in ("anthropic", "claude"):
        meta: Dict[str, Any] = {
            "upstream_provider": "anthropic",
            "anthropic_api_key": api_key,
            "anthropic_model": model,
            "openai_messages": serialized,
            "anthropic_max_tokens": max_tok,
            "anthropic_temperature": temp,
        }
        if fb_model:
            meta["anthropic_fallback_model"] = fb_model
    else:
        openai_url = _openai_chat_url(base_url)
        meta = {
            "upstream_provider": "openai_compatible",
            "openai_url": openai_url,
            "openai_api_key": api_key,
            "openai_model": model,
            "openai_messages": serialized,
            "openai_temperature": temp,
            "openai_max_tokens": max_tok,
        }
        if fb_model:
            meta["openai_fallback_model"] = fb_model

    meta["planner_transport"] = "a2a_adapter"
    stub_input: Dict[str, Any] = {
        "schema_version": PLANNER_CHAT_SCHEMA,
        "relay": True,
        "message_count": len(serialized),
    }
    result = await execute_via_a2a(
        adapter_url,
        stub_input,
        api_key=hop_key,
        blocking=True,
        timeout=timeout,
        adapter_metadata=meta,
    )
    return (result.get("content") or "").strip()


async def planner_chat_completion(
    messages: List[Dict[str, Any]],
    *,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Run chat completion using platform planner config.
    Raises on configuration error or HTTP failure.
    """
    if not is_agent_planner_configured():
        raise RuntimeError(
            "Agent planner is not configured (set AGENT_PLANNER_API_KEY and transport-specific URLs)"
        )

    t0 = time.perf_counter()
    runtime = _planner_runtime_transport_ctx.get() or {}
    transport = _normalize_planner_transport(runtime.get("transport"))
    transport_reason = (runtime.get("reason") or "runtime").strip() or "runtime"
    default_provider = (getattr(settings, "AGENT_PLANNER_PROVIDER", None) or "openai_compatible").strip().lower()
    max_tok = max_tokens if max_tokens is not None else int(getattr(settings, "AGENT_PLANNER_MAX_TOKENS", 4096) or 4096)
    temp = temperature if temperature is not None else float(getattr(settings, "AGENT_PLANNER_TEMPERATURE", 0.3) or 0.3)
    verify = httpx_verify_parameter()

    def _build_profile(secondary: bool = False) -> Dict[str, Any]:
        if not secondary:
            provider = default_provider
            api_key = (getattr(settings, "AGENT_PLANNER_API_KEY", None) or "").strip()
            model = (getattr(settings, "AGENT_PLANNER_MODEL", None) or "").strip() or "gpt-4o-mini"
            fb_model = (getattr(settings, "AGENT_PLANNER_FALLBACK_MODEL", None) or "").strip() or None
            if not fb_model:
                fb_model = (getattr(settings, "LLM_HTTP_FALLBACK_MODEL", None) or "").strip() or None
            return {
                "name": "primary",
                "provider": provider,
                "api_key": api_key,
                "model": model,
                "fallback_model": fb_model,
                "base_url": (getattr(settings, "AGENT_PLANNER_BASE_URL", None) or "").strip(),
                "timeout": float(getattr(settings, "AGENT_PLANNER_HTTP_TIMEOUT_SECONDS", 120.0) or 120.0),
                "native_a2a_url": (getattr(settings, "AGENT_PLANNER_A2A_URL", None) or "").strip(),
                "native_a2a_api_key": (getattr(settings, "AGENT_PLANNER_A2A_API_KEY", None) or "").strip() or None,
                "adapter_url": (getattr(settings, "AGENT_PLANNER_ADAPTER_URL", None) or "").strip(),
                "adapter_api_key": (getattr(settings, "AGENT_PLANNER_A2A_API_KEY", None) or "").strip() or None,
            }
        provider = (
            (getattr(settings, "AGENT_PLANNER_SECONDARY_PROVIDER", None) or "").strip().lower()
            or default_provider
        )
        api_key = (getattr(settings, "AGENT_PLANNER_SECONDARY_API_KEY", None) or "").strip()
        model = (
            (getattr(settings, "AGENT_PLANNER_SECONDARY_MODEL", None) or "").strip()
            or (getattr(settings, "AGENT_PLANNER_MODEL", None) or "").strip()
            or "gpt-4o-mini"
        )
        fb_model = (getattr(settings, "AGENT_PLANNER_SECONDARY_FALLBACK_MODEL", None) or "").strip() or None
        if not fb_model:
            fb_model = (getattr(settings, "AGENT_PLANNER_FALLBACK_MODEL", None) or "").strip() or None
        if not fb_model:
            fb_model = (getattr(settings, "LLM_HTTP_FALLBACK_MODEL", None) or "").strip() or None
        primary_base = _nonnull_str(getattr(settings, "AGENT_PLANNER_BASE_URL", None))
        primary_native_a2a_url = _nonnull_str(getattr(settings, "AGENT_PLANNER_A2A_URL", None))
        primary_native_a2a_key = _nonnull_str(getattr(settings, "AGENT_PLANNER_A2A_API_KEY", None)) or None
        primary_adapter_url = _nonnull_str(getattr(settings, "AGENT_PLANNER_ADAPTER_URL", None))
        primary_adapter_key = _nonnull_str(getattr(settings, "AGENT_PLANNER_A2A_API_KEY", None)) or None
        secondary_base = _nonnull_str(getattr(settings, "AGENT_PLANNER_SECONDARY_BASE_URL", None))
        secondary_native_a2a_url = _nonnull_str(getattr(settings, "AGENT_PLANNER_SECONDARY_A2A_URL", None))
        secondary_native_a2a_key = _nonnull_str(getattr(settings, "AGENT_PLANNER_SECONDARY_A2A_API_KEY", None)) or None
        secondary_adapter_url = _nonnull_str(getattr(settings, "AGENT_PLANNER_SECONDARY_ADAPTER_URL", None))
        secondary_adapter_key = _nonnull_str(getattr(settings, "AGENT_PLANNER_SECONDARY_A2A_API_KEY", None)) or None
        return {
            "name": "secondary",
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "fallback_model": fb_model,
            "base_url": secondary_base or primary_base,
            "timeout": float(getattr(settings, "AGENT_PLANNER_SECONDARY_HTTP_TIMEOUT_SECONDS", 120.0) or 120.0),
            "native_a2a_url": secondary_native_a2a_url or primary_native_a2a_url,
            "native_a2a_api_key": secondary_native_a2a_key or primary_native_a2a_key,
            "adapter_url": secondary_adapter_url or primary_adapter_url,
            "adapter_api_key": secondary_adapter_key or primary_adapter_key,
        }

    def _latency_ms() -> float:
        return round((time.perf_counter() - t0) * 1000.0, 2)

    async def _run_with_profile(profile: Dict[str, Any]) -> str:
        provider = str(profile.get("provider") or default_provider).strip().lower()
        api_key = str(profile.get("api_key") or "").strip()
        model = str(profile.get("model") or "gpt-4o-mini").strip() or "gpt-4o-mini"
        fb_model = profile.get("fallback_model")
        timeout = float(profile.get("timeout") or 120.0)
        base_url = str(profile.get("base_url") or "").strip()
        native_a2a_url = str(profile.get("native_a2a_url") or "").strip()
        native_a2a_api_key = profile.get("native_a2a_api_key")
        adapter_url = str(profile.get("adapter_url") or "").strip()
        adapter_api_key = profile.get("adapter_api_key")

        if transport == PLANNER_TRANSPORT_NATIVE_A2A:
            return await _planner_chat_completion_native_a2a(
                messages,
                a2a_url=native_a2a_url,
                hop_key=native_a2a_api_key,
                temperature=temp,
                max_tokens=max_tok,
                timeout=timeout,
            )

        if transport == PLANNER_TRANSPORT_A2A_ADAPTER:
            if not api_key:
                raise RuntimeError("AGENT_PLANNER_API_KEY is required for a2a_adapter transport (upstream credential)")
            return await _planner_chat_completion_via_dedicated_adapter(
                messages,
                adapter_url=adapter_url,
                adapter_hop_key=adapter_api_key,
                base_url=base_url,
                provider=provider,
                api_key=api_key,
                model=model,
                max_tok=max_tok,
                temp=temp,
                fb_model=fb_model,
                timeout=timeout,
            )

        # --- direct HTTP ---
        if not api_key:
            raise RuntimeError("AGENT_PLANNER_API_KEY is required for direct transport")
        if provider in ("anthropic", "claude"):
            return await _anthropic_messages(
                messages,
                api_key=api_key,
                model=model,
                max_tokens=max_tok,
                temperature=temp,
                verify=verify,
                timeout=timeout,
                fallback_model=fb_model,
            )
        return await _openai_compatible_planner_completion(
            messages,
            base_url=base_url,
            api_key=api_key,
            model=model,
            max_tok=max_tok,
            temp=temp,
            fb_model=fb_model,
            verify=verify,
            timeout=timeout,
        )

    primary = _build_profile(False)
    secondary_enabled = bool(getattr(settings, "AGENT_PLANNER_SECONDARY_ENABLED", False))
    secondary = _build_profile(True) if secondary_enabled else None

    active_profile = primary
    try:
        out = await _run_with_profile(primary)
        logger.info(
            "planner_llm_ok latency_ms=%s planner_profile=%s transport=%s transport_reason=%s provider=%s model=%s",
            _latency_ms(),
            primary.get("name"),
            transport,
            transport_reason,
            primary.get("provider"),
            primary.get("model"),
        )
        return out
    except Exception as primary_exc:
        if secondary and (secondary.get("api_key") or "").strip():
            logger.warning(
                "planner_llm_primary_failed latency_ms=%s transport=%s transport_reason=%s primary_provider=%s primary_model=%s exc_type=%s; switching_to=secondary",
                _latency_ms(),
                transport,
                transport_reason,
                primary.get("provider"),
                primary.get("model"),
                type(primary_exc).__name__,
            )
            active_profile = secondary
            try:
                out = await _run_with_profile(secondary)
                logger.info(
                    "planner_llm_ok latency_ms=%s planner_profile=%s transport=%s transport_reason=%s provider=%s model=%s",
                    _latency_ms(),
                    secondary.get("name"),
                    transport,
                    transport_reason,
                    secondary.get("provider"),
                    secondary.get("model"),
                )
                return out
            except Exception as secondary_exc:
                raise secondary_exc from primary_exc
        raise

    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        logger.warning(
            "planner_llm_http_error latency_ms=%s planner_profile=%s transport=%s transport_reason=%s provider=%s model=%s http_status=%s",
            _latency_ms(),
            active_profile.get("name"),
            transport,
            transport_reason,
            active_profile.get("provider"),
            active_profile.get("model"),
            status,
        )
        raise
    except Exception as e:
        logger.warning(
            "planner_llm_error latency_ms=%s planner_profile=%s transport=%s transport_reason=%s provider=%s model=%s exc_type=%s",
            _latency_ms(),
            active_profile.get("name"),
            transport,
            transport_reason,
            active_profile.get("provider"),
            active_profile.get("model"),
            type(e).__name__,
        )
        raise
