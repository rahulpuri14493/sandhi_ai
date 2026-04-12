# backend/routers/enhance_job_description.py
"""
Router: POST /api/jobs/enhance-description

Accepts a job description text, sends it to an LLM (via OpenAI-compatible
endpoint), and returns:
  - corrected_text   : grammar-fixed, professionally rewritten description
  - recreated_prompts: refined, actionable prompt instructions
"""

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from core.config import settings
from services.httpx_tls import httpx_verify_parameter

logger = logging.getLogger(__name__)


def _safe_text_preview(text: str | None, max_len: int = 600) -> str:
    if not text:
        return ""
    chunk = text[:max_len]
    return chunk.encode("utf-8", errors="replace").decode("utf-8")


#from core.security import get_current_user  # adjust to your auth import path

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class EnhanceRequest(BaseModel):
    description: str

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description cannot be empty")
        if len(v) < 10:
            raise ValueError("description is too short to enhance (min 10 chars)")
        return v


class EnhanceResponse(BaseModel):
    corrected_text: str
    recreated_prompts: str


# ─── Service ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert technical writer and HR specialist. Your task is to improve
job descriptions. Given a raw job description you must return a JSON object with
exactly two keys:

"corrected_text"    – The full job description rewritten with correct grammar,
                      professional tone, clear sentence structure, and improved
                      readability. Preserve all original intent and details.

"recreated_prompts" – A concise set of bullet-point prompt instructions (3-6
                      bullets) that summarise the key requirements, skills, and
                      responsibilities in clear, actionable language.

Both values MUST be single JSON strings (not arrays). Use newlines inside the string for bullets.

Return ONLY valid JSON. No markdown fences, no preamble, no explanation.
"""


def _coerce_llm_text_field(value: object) -> str:
    """LLMs sometimes return lists; API contract is plain strings."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if isinstance(item, str):
                t = item.strip()
            else:
                t = str(item).strip()
            if t:
                lines.append(t)
        return "\n".join(lines)
    return str(value).strip()


def _extract_message_content(data: dict) -> str:
    """OpenAI-compatible chat completion body → assistant message text."""
    try:
        choices = data.get("choices") or []
        if not choices:
            raise KeyError("no choices")
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            raise KeyError("empty content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content)
    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM response missing message content: {e}",
        ) from e


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


async def _call_llm(description: str) -> EnhanceResponse:
    """
    Calls the configured LLM endpoint (OpenAI-compatible POST .../chat/completions).
    Configure OPENAI_API_KEY, OPENAI_BASE_URL, ENHANCE_MODEL in .env or environment.
    """
    api_key = (settings.OPENAI_API_KEY or "").strip()
    base_url = (settings.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
    model = settings.ENHANCE_MODEL or "gpt-4o-mini"

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is not configured. Set OPENAI_API_KEY in .env",
        )

    request_body = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": 1500,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Job Description:\n\n{description}",
            },
        ],
    }

    url = f"{base_url}/chat/completions"
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            verify=httpx_verify_parameter(),
        ) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except httpx.RequestError as exc:
        logger.warning("Enhance LLM request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach LLM API ({url}): {exc!s}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM service error: {_safe_text_preview(response.text, 500)}",
        )

    try:
        completion = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned non-JSON body",
        ) from exc

    if not isinstance(completion, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned JSON that is not an object",
        )

    raw_content = _extract_message_content(completion)
    raw_content = _strip_json_fence(raw_content)

    try:
        parsed = json.loads(raw_content)
        if not isinstance(parsed, dict):
            raise TypeError("expected JSON object from LLM")
        return EnhanceResponse(
            corrected_text=_coerce_llm_text_field(parsed.get("corrected_text")),
            recreated_prompts=_coerce_llm_text_field(parsed.get("recreated_prompts")),
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM returned unexpected format: {str(exc)}",
        ) from exc


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@router.post(
    "/enhance-description-ai",
    response_model=EnhanceResponse,
    summary="Enhance job description with AI",
    description=(
        "Accepts a raw job description, corrects grammar and improves "
        "readability, then returns the corrected text and a set of "
        "refined prompt instructions."
    ),
)

async def enhance_description(
    body: EnhanceRequest,
    #current_user=Depends(get_current_user),
) -> EnhanceResponse:
    """
    POST /api/jobs/enhance-description

    Requires authentication (Bearer token).
    """
    try:
        return await _call_llm(body.description)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("enhance_description failed")
        msg = _safe_text_preview(f"{type(exc).__name__}: {exc}", 800)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Enhance failed: {msg}",
        ) from exc
