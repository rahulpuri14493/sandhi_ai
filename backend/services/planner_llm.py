"""
Platform Agent Planner LLM client (Issue #62).

Unified completion API for admin-configured providers. API keys come from settings
(env / secret manager); never log key values.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from core.config import settings
from services.httpx_tls import httpx_verify_parameter

logger = logging.getLogger(__name__)


def is_agent_planner_configured() -> bool:
    """True when platform planner should handle BRD analysis, split, and tool suggestion."""
    if not getattr(settings, "AGENT_PLANNER_ENABLED", True):
        return False
    key = (getattr(settings, "AGENT_PLANNER_API_KEY", None) or "").strip()
    return bool(key)


def get_planner_public_meta() -> Dict[str, Any]:
    """Safe to return to authenticated clients (no secrets)."""
    return {
        "provider": (getattr(settings, "AGENT_PLANNER_PROVIDER", None) or "openai_compatible").strip(),
        "model": (getattr(settings, "AGENT_PLANNER_MODEL", None) or "").strip() or "gpt-4o-mini",
        "base_url_configured": bool((getattr(settings, "AGENT_PLANNER_BASE_URL", None) or "").strip()),
    }


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


async def _openai_compatible_planner_completion(
    messages: List[Dict[str, Any]],
    *,
    api_key: str,
    model: str,
    max_tok: int,
    temp: float,
    fb_model: Optional[str],
    verify: Any,
    timeout: float,
) -> str:
    base = (getattr(settings, "AGENT_PLANNER_BASE_URL", None) or "").strip()
    url = _openai_chat_url(base)
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
        raise RuntimeError("Agent planner is not configured (set AGENT_PLANNER_API_KEY)")

    t0 = time.perf_counter()
    provider = (getattr(settings, "AGENT_PLANNER_PROVIDER", None) or "openai_compatible").strip().lower()
    api_key = (getattr(settings, "AGENT_PLANNER_API_KEY", None) or "").strip()
    model = (getattr(settings, "AGENT_PLANNER_MODEL", None) or "").strip() or "gpt-4o-mini"
    max_tok = max_tokens if max_tokens is not None else int(getattr(settings, "AGENT_PLANNER_MAX_TOKENS", 4096) or 4096)
    temp = temperature if temperature is not None else float(getattr(settings, "AGENT_PLANNER_TEMPERATURE", 0.3) or 0.3)
    fb_model = (getattr(settings, "AGENT_PLANNER_FALLBACK_MODEL", None) or "").strip() or None
    if not fb_model:
        fb_model = (getattr(settings, "LLM_HTTP_FALLBACK_MODEL", None) or "").strip() or None

    verify = httpx_verify_parameter()
    timeout = float(getattr(settings, "AGENT_PLANNER_HTTP_TIMEOUT_SECONDS", 120.0) or 120.0)

    def _latency_ms() -> float:
        return round((time.perf_counter() - t0) * 1000.0, 2)

    try:
        if provider in ("anthropic", "claude"):
            out = await _anthropic_messages(
                messages,
                api_key=api_key,
                model=model,
                max_tokens=max_tok,
                temperature=temp,
                verify=verify,
                timeout=timeout,
                fallback_model=fb_model,
            )
        else:
            out = await _openai_compatible_planner_completion(
                messages,
                api_key=api_key,
                model=model,
                max_tok=max_tok,
                temp=temp,
                fb_model=fb_model,
                verify=verify,
                timeout=timeout,
            )
        logger.info(
            "planner_llm_ok latency_ms=%s provider=%s model=%s",
            _latency_ms(),
            provider,
            model,
        )
        return out
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        logger.warning(
            "planner_llm_http_error latency_ms=%s provider=%s model=%s http_status=%s",
            _latency_ms(),
            provider,
            model,
            status,
        )
        raise
    except Exception as e:
        logger.warning(
            "planner_llm_error latency_ms=%s provider=%s model=%s exc_type=%s",
            _latency_ms(),
            provider,
            model,
            type(e).__name__,
        )
        raise


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


def parse_json_loose(text: str) -> Any:
    """Parse JSON from model output; strip markdown fences if present."""
    content = (text or "").strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1] if "```" in content else content
        if content.startswith("json"):
            content = content[4:].lstrip()
        content = content.strip()
    return json.loads(content)
