"""
Rule-driven tool assignment from task type + requirements.

Registry-based selection and ordering. Optional LLM merge when ``TOOL_ASSIGNMENT_USE_LLM``
is enabled and ``llm_suggested_tool_names`` is present on step input.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from core.config import settings
from models.agent import Agent
from schemas.sandhi_a2a_task import AssignedToolMeta
from services.tool_assignment_registry import ToolAssignmentRegistry, get_tool_assignment_registry

logger = logging.getLogger(__name__)

# Planner / BRD may set ``task_type`` on step input_data
_TASK_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.IGNORECASE)


def infer_task_type(input_data: Dict[str, Any]) -> str:
    explicit = (input_data or {}).get("task_type")
    if explicit is not None and str(explicit).strip():
        t = str(explicit).strip().lower()
        if _TASK_TYPE_RE.match(t):
            return t
    task = str((input_data or {}).get("assigned_task") or "").strip().lower()
    if not task:
        return "default"
    first = task.split()[0] if task.split() else ""
    first = re.sub(r"[^a-z0-9_]+", "", first)
    if first and _TASK_TYPE_RE.match(first):
        return first
    return "default"


def _tool_meta_from_descriptor(t: Dict[str, Any]) -> AssignedToolMeta:
    name = str((t or {}).get("name") or "").strip()
    hints: Dict[str, Any] = {}
    if (t or {}).get("source"):
        hints["source"] = t.get("source")
    return AssignedToolMeta(
        tool_name=name,
        platform_tool_id=t.get("platform_tool_id"),
        external_tool_name=(t.get("external_tool_name") or None),
        tool_type=(t.get("tool_type") or None),
        connection_id=t.get("connection_id"),
        input_schema=t.get("input_schema") if isinstance(t.get("input_schema"), dict) else None,
        execution_hints=hints or None,
    )


def assign_tools_for_step(
    *,
    input_data: Dict[str, Any],
    agent: Agent,
    available_mcp_tools: List[Dict[str, Any]],
    registry: Optional[ToolAssignmentRegistry] = None,
) -> Tuple[List[Dict[str, Any]], List[AssignedToolMeta], str, bool]:
    """
    Returns:
        ordered tool descriptors (same shape as MCP list),
        structured assigned_tools for sandhi_a2a_task,
        assignment_source,
        assignment_flagged
    """
    if not getattr(settings, "TOOL_ASSIGNMENT_ENABLED", True):
        meta = [_tool_meta_from_descriptor(t) for t in available_mcp_tools if isinstance(t, dict) and t.get("name")]
        return list(available_mcp_tools), meta, "passthrough", False

    reg = registry or get_tool_assignment_registry()
    task_type = infer_task_type(input_data)
    requirements = input_data.get("assignment_requirements")
    if requirements is not None and not isinstance(requirements, dict):
        requirements = None

    rule = reg.rule_for_task_type(task_type)
    flagged = False
    max_tools: int
    preferred: List[str]

    if rule:
        max_tools = rule.max_tools
        preferred = list(rule.preferred_tool_types)
        source = "registry"
    else:
        max_tools = reg.fallback.max_tools
        preferred = []
        source = "registry_fallback"
        flagged = bool(reg.fallback.flag_unmatched)

    tools = [t for t in (available_mcp_tools or []) if isinstance(t, dict) and (t.get("name") or "").strip()]

    def score(t: Dict[str, Any]) -> Tuple[int, str]:
        tt = str(t.get("tool_type") or "").strip().lower()
        pref_rank = len(preferred)
        if tt in preferred:
            pref_rank = preferred.index(tt)
        return (pref_rank, str(t.get("name") or ""))

    if preferred:
        matched = [t for t in tools if str(t.get("tool_type") or "").strip().lower() in preferred]
        rest = [t for t in tools if t not in matched]
        matched.sort(key=score)
        ordered = matched + rest
    else:
        ordered = sorted(tools, key=lambda t: str(t.get("name") or ""))

    ordered = ordered[:max_tools]
    assigned_meta = [_tool_meta_from_descriptor(t) for t in ordered]

    # Optional LLM path: reserved — merge planner-suggested names when present
    if getattr(settings, "TOOL_ASSIGNMENT_USE_LLM", False):
        suggested = input_data.get("llm_suggested_tool_names")
        if isinstance(suggested, list) and suggested:
            name_order = [str(x).strip() for x in suggested if str(x).strip()]
            name_set = set(name_order)
            if name_set:
                by_name = {str(t.get("name") or "").strip(): t for t in tools if isinstance(t, dict)}
                front: List[Dict[str, Any]] = []
                for n in name_order:
                    t = by_name.get(n)
                    if t is not None and t not in front:
                        front.append(t)
                back = [t for t in ordered if t not in front]
                ordered = (front + back)[:max_tools]
                assigned_meta = [_tool_meta_from_descriptor(t) for t in ordered]
                source = "llm"

    if isinstance(requirements, dict):
        must_names = requirements.get("must_include_tool_names")
        if isinstance(must_names, list) and must_names:
            must = {str(x).strip() for x in must_names if str(x).strip()}
            must_tools = [t for t in tools if str(t.get("name") or "").strip() in must]
            rest = [t for t in ordered if t not in must_tools]
            ordered = (must_tools + rest)[:max_tools]
            assigned_meta = [_tool_meta_from_descriptor(t) for t in ordered]

    logger.debug(
        "tool_assignment task_type=%s source=%s flagged=%s tool_count=%s agent_id=%s",
        task_type,
        source,
        flagged,
        len(ordered),
        agent.id,
    )
    return ordered, assigned_meta, source, flagged
