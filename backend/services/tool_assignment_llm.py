"""
LLM-assisted tool name selection for assignment (allowlist-only).

Uses the platform Agent Planner when configured. Requires ``TOOL_ASSIGNMENT_USE_LLM`` and
``TOOL_ASSIGNMENT_LLM_PICK_TOOLS`` in settings; the executor injects ``llm_suggested_tool_names``
before ``assign_tools_for_step``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from services.planner_llm import planner_chat_completion

logger = logging.getLogger(__name__)

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


def _parse_tool_names_json(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        arr = data.get("tool_names")
        if not isinstance(arr, list):
            return []
        return [str(x).strip() for x in arr if str(x).strip()]
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    return []


async def suggest_tool_names_with_llm(
    *,
    job_title: str,
    assigned_task: str,
    task_type: str,
    tools: List[Dict[str, Any]],
    max_names: int,
) -> List[str]:
    """Return tool ``name`` values chosen by the planner, filtered to the provided allowlist."""
    allowed = [str(t.get("name") or "").strip() for t in tools if isinstance(t, dict) and (t.get("name") or "").strip()]
    if not allowed:
        return []
    allow_set = set(allowed)
    cap = max(1, min(int(max_names or 12), len(allowed)))
    lines: List[str] = []
    for t in tools[:80]:
        if not isinstance(t, dict):
            continue
        nm = str(t.get("name") or "").strip()
        if not nm:
            continue
        tt = str(t.get("tool_type") or "")
        desc = (str(t.get("description") or "") or "")[:160].replace("\n", " ")
        lines.append(f"- {nm} (type={tt}) {desc}")

    user = (
        f"task_type: {task_type or 'default'}\n"
        f"job_title: {job_title}\n"
        f"assigned_task:\n{assigned_task}\n\n"
        "Choose up to "
        f"{cap} tool function names that best fit this step. "
        "Use only names from the list below.\n\n"
        "Tools:\n"
        + "\n".join(lines)
        + '\n\nReply with JSON only: {"tool_names": ["name1", ...]}'
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You select MCP tool function names for one workflow step. "
                "Output JSON only: an object with key tool_names (array of strings). "
                "Every string must be exactly one of the tool names from the user message; "
                "no invented names, no markdown, no commentary."
            ),
        },
        {"role": "user", "content": user},
    ]
    max_tok = min(800, 120 + cap * 24)
    out = await planner_chat_completion(messages, temperature=0.2, max_tokens=max_tok)
    picked = _parse_tool_names_json(out)
    filtered = [n for n in picked if n in allow_set]
    # Preserve order, dedupe
    seen: set[str] = set()
    unique: List[str] = []
    for n in filtered:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique[:cap]
