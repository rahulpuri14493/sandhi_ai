"""
Shared httpx usage for OpenAI-compatible chat endpoints (task/tool split when not using platform planner).

Retries on transient network errors only; HTTP 4xx/5xx are returned to callers.
Optional single retry with a fallback model after 429 or 5xx (see LLM_HTTP_FALLBACK_MODEL).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Union

import httpx

from services.httpx_tls import httpx_verify_parameter

logger = logging.getLogger(__name__)


async def _post_once(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    *,
    timeout: float,
    verify: Union[bool, str],
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
        return await client.post(url, json=payload, headers=headers)


async def post_openai_compatible_raw(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    *,
    timeout: float = 60.0,
    max_retries: int = 2,
    fallback_model: Optional[str] = None,
) -> httpx.Response:
    """
    POST JSON to an OpenAI-compatible URL. Retries on connection/timeout errors.
    Does not raise on HTTP error status — caller decides (e.g. fallback on 4xx).

    If ``fallback_model`` is set and the first successful HTTP response has status
    429 or 5xx, performs one additional request with ``model`` replaced.
    """
    verify = httpx_verify_parameter()
    last_exc: Optional[BaseException] = None
    resp: Optional[httpx.Response] = None
    for attempt in range(max_retries + 1):
        try:
            resp = await _post_once(url, headers, payload, timeout=timeout, verify=verify)
            break
        except httpx.RequestError as e:
            last_exc = e
            if attempt < max_retries:
                delay = 0.5 * (2**attempt)
                logger.warning(
                    "httpx POST %s attempt %s failed (%s), retry in %.1fs",
                    url[:80],
                    attempt + 1,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise
    assert resp is not None
    fb = (fallback_model or "").strip()
    if fb and resp.status_code in (429, 500, 502, 503, 504):
        p2 = dict(payload)
        p2["model"] = fb
        logger.warning(
            "LLM endpoint returned %s; retrying once with fallback model %s",
            resp.status_code,
            fb,
        )
        try:
            resp = await _post_once(url, headers, p2, timeout=timeout, verify=verify)
        except httpx.RequestError as e:
            logger.warning("Fallback model request failed: %s", e)
    return resp
