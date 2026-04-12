"""
Agent ↔ MCP tool compatibility.

Uses optional capability strings on ``Agent.capabilities`` (JSON list) so operators
can constrain which tool families an agent is allowed to see without code changes.

Supported patterns (case-insensitive on keys; type lists are lowercased):
- ``mcp:allow_types:postgres,mysql`` — only tools whose ``tool_type`` is listed
- ``mcp:deny_types:s3,slack`` — drop tools whose ``tool_type`` is listed
- ``mcp:allow_connection_ids:1,2`` — only tools whose integer ``connection_id`` is in that set (tools without ``connection_id`` are dropped)
- ``mcp:deny_connection_ids:5`` — drop tools whose ``connection_id`` is in that set
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from models.agent import Agent

logger = logging.getLogger(__name__)


def _parse_connection_ids(segment: str) -> Set[int]:
    out: Set[int] = set()
    for part in segment.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except (TypeError, ValueError):
            continue
    return out


def _capability_sets(capabilities: Any) -> Tuple[Optional[Set[str]], Optional[Set[str]], Optional[Set[int]], Set[int]]:
    allow: Optional[Set[str]] = None
    deny: Set[str] = set()
    allow_conn: Optional[Set[int]] = None
    deny_conn: Set[int] = set()
    if not capabilities:
        return None, None, None, deny_conn
    if not isinstance(capabilities, list):
        return None, None, None, deny_conn
    for raw in capabilities:
        s = str(raw or "").strip()
        low = s.lower()
        if low.startswith("mcp:allow_types:"):
            parts = s.split(":", 2)
            if len(parts) >= 3:
                ids = {p.strip().lower() for p in parts[2].split(",") if p.strip()}
                if ids:
                    allow = ids
        elif low.startswith("mcp:deny_types:"):
            parts = s.split(":", 2)
            if len(parts) >= 3:
                deny.update(p.strip().lower() for p in parts[2].split(",") if p.strip())
        elif low.startswith("mcp:allow_connection_ids:"):
            parts = s.split(":", 2)
            if len(parts) >= 3:
                cids = _parse_connection_ids(parts[2])
                if cids:
                    allow_conn = cids
        elif low.startswith("mcp:deny_connection_ids:"):
            parts = s.split(":", 2)
            if len(parts) >= 3:
                deny_conn.update(_parse_connection_ids(parts[2]))
    deny_out: Optional[Set[str]] = deny if deny else None
    return allow, deny_out, allow_conn, deny_conn


def _tool_type(tool: Dict[str, Any]) -> str:
    return str((tool or {}).get("tool_type") or "").strip().lower()


def _tool_connection_id(tool: Dict[str, Any]) -> Optional[int]:
    v = (tool or {}).get("connection_id")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def filter_tools_for_agent(agent: Agent, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return tools compatible with agent capabilities; order preserved."""
    allow, deny, allow_conn, deny_conn = _capability_sets(getattr(agent, "capabilities", None))
    out: List[Dict[str, Any]] = []
    dropped = 0
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        tt = _tool_type(t)
        if allow is not None and tt and tt not in allow:
            dropped += 1
            continue
        if deny and tt and tt in deny:
            dropped += 1
            continue
        cid = _tool_connection_id(t)
        if allow_conn is not None:
            if cid is None or cid not in allow_conn:
                dropped += 1
                continue
        if deny_conn and cid is not None and cid in deny_conn:
            dropped += 1
            continue
        out.append(t)
    if dropped:
        logger.debug(
            "agent_tool_compatibility_filtered agent_id=%s dropped=%s remaining=%s",
            agent.id,
            dropped,
            len(out),
        )
    return out


def validate_tools_for_agent(agent: Agent, tools: List[Dict[str, Any]]) -> List[str]:
    """Human-readable issues when the filtered list would drop all tools but the job had tools."""
    allow, deny, allow_conn, deny_conn = _capability_sets(getattr(agent, "capabilities", None))
    if allow is None and not deny and allow_conn is None and not deny_conn:
        return []
    kept = filter_tools_for_agent(agent, tools)
    if (tools or []) and not kept:
        return [
            "Agent capability constraints (mcp:allow_types / mcp:deny_types / "
            "mcp:allow_connection_ids / mcp:deny_connection_ids) "
            "excluded all MCP tools for this step; widen capabilities or tool allowlists."
        ]
    return []
