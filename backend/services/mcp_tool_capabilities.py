"""
Classify platform MCP tool types for UX and BRD-aligned tool splitting.

- interactive: tools the agent calls during a step (query, list, etc.).
- artifact_write: types supported by platform output contract / execute_artifact_write.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Primarily retrieval / search during a step (still may have side effects in some providers).
_READ_BIAS_TYPES = frozenset(
    {
        "vector_db",
        "pinecone",
        "weaviate",
        "qdrant",
        "chroma",
        "elasticsearch",
        "pageindex",
    }
)

# SQL / tabular: interactive read + write possible (DML) — policy is prompt + DB permissions.
_SQL_TYPES = frozenset(
    {
        "postgres",
        "mysql",
        "sqlserver",
        "snowflake",
        "databricks",
        "bigquery",
    }
)

# Object / file surfaces that support interactive read+write and artifact pipeline writes.
_OBJECT_TYPES = frozenset({"s3", "minio", "ceph", "azure_blob", "gcs", "filesystem"})

# Typically read-biased integrations for interactive use.
_API_READ_BIAS = frozenset({"github", "notion"})

# List/discover (read-like) and send/post (write-like): Slack, Teams, SMTP validate + send.
_MESSAGING = frozenset({"slack", "teams", "smtp"})

# Generic HTTP — read/write depends on usage.
_REST = frozenset({"rest_api"})


def normalize_tool_type(tool_type: str) -> str:
    return (tool_type or "").strip().lower()


def tool_access_summary(tool_type: str) -> Dict[str, Any]:
    """
    Return labels for UI and planning.

    - tier: coarse bucket for splitting heuristics.
    - interactive_read_primary: good for early pipeline / research steps.
    - supports_artifact_platform_write: can appear in output_contract write_targets.
    """
    tt = normalize_tool_type(tool_type)
    summary = {
        "tier": "general",
        "interactive_read_primary": False,
        "supports_interactive_write": False,
        "supports_artifact_platform_write": False,
        "label": "Mixed",
        "hint": "Interactive tool use during the step; follow least-privilege in prompts.",
    }
    if tt in _READ_BIAS_TYPES:
        summary.update(
            {
                "tier": "search",
                "interactive_read_primary": True,
                "supports_interactive_write": False,
                "supports_artifact_platform_write": False,
                "label": "Search / retrieve",
                "hint": "Best for lookup, RAG, and evidence gathering early in the workflow.",
            }
        )
        return summary
    if tt in _SQL_TYPES:
        summary.update(
            {
                "tier": "sql",
                "interactive_read_primary": True,
                "supports_interactive_write": True,
                "supports_artifact_platform_write": True,
                "label": "SQL (read + write)",
                "hint": "SELECT vs DML is enforced by your SQL and session; use output contract for bulk artifact loads.",
            }
        )
        return summary
    if tt in _OBJECT_TYPES:
        summary.update(
            {
                "tier": "object",
                "interactive_read_primary": True,
                "supports_interactive_write": True,
                "supports_artifact_platform_write": True,
                "label": "Files / object storage",
                "hint": "Interactive list/get/put; platform mode can copy step artifacts to bucket/prefix.",
            }
        )
        return summary
    if tt in _API_READ_BIAS:
        summary.update(
            {
                "tier": "integration_read",
                "interactive_read_primary": True,
                "supports_interactive_write": False,
                "supports_artifact_platform_write": False,
                "label": "Read-mostly API",
                "hint": "Typically fetch/search; not used for output contract writes.",
            }
        )
        return summary
    if tt in _MESSAGING:
        summary.update(
            {
                "tier": "messaging",
                "interactive_read_primary": True,
                "supports_interactive_write": True,
                "supports_artifact_platform_write": False,
                "label": "Messaging (read + write)",
                "hint": (
                    "Read-like: list channels/teams, SMTP validate. "
                    "Write-like: send messages or email. Guardrails classify by action; scope per step."
                ),
            }
        )
        return summary
    if tt in _REST:
        summary.update(
            {
                "tier": "rest",
                "interactive_read_primary": True,
                "supports_interactive_write": True,
                "supports_artifact_platform_write": False,
                "label": "REST API",
                "hint": "Read/write depends on routes you allow; scope narrowly per step.",
            }
        )
        return summary
    return summary


def partition_tools_for_fallback(
    platform_tools: List[Dict[str, Any]], num_agents: int
) -> List[Dict[str, Any]]:
    """
    Deterministic split when LLM is unavailable: search tools → earlier steps, persistence → last step.
    platform_tools: dicts with at least id, tool_type (name optional).
    """
    if num_agents <= 0:
        return []
    if num_agents == 1:
        return [
            {
                "agent_index": 0,
                "platform_tool_ids": [t["id"] for t in platform_tools],
                "rationale": "Single agent: all job tools assigned.",
            }
        ]

    by_tier: Dict[str, List[int]] = {"search": [], "other": [], "persistence": []}
    for t in platform_tools:
        tid = t.get("id")
        if tid is None:
            continue
        tt = normalize_tool_type(str(t.get("tool_type", "")))
        s = tool_access_summary(tt)
        tier = s.get("tier")
        if tier == "search" or s.get("interactive_read_primary") and not s.get("supports_artifact_platform_write"):
            by_tier["search"].append(int(tid))
        elif s.get("supports_artifact_platform_write") or tier in ("sql", "object"):
            by_tier["persistence"].append(int(tid))
        else:
            by_tier["other"].append(int(tid))

    n = num_agents
    early_pool = list(dict.fromkeys(by_tier["search"] + by_tier["other"]))
    pers_ids = list(dict.fromkeys(by_tier["persistence"]))

    out: List[Dict[str, Any]] = [
        {"agent_index": i, "platform_tool_ids": [], "rationale": ""} for i in range(n)
    ]

    if n == 1:
        out[0]["platform_tool_ids"] = list(dict.fromkeys(early_pool + pers_ids))
        out[0]["rationale"] = "Single agent: all tools assigned."
        return out

    for idx, tid in enumerate(early_pool):
        ai = idx % (n - 1)
        out[ai]["platform_tool_ids"].append(tid)
    for tid in pers_ids:
        out[n - 1]["platform_tool_ids"].append(tid)

    for i, row in enumerate(out):
        row["platform_tool_ids"] = list(dict.fromkeys(row["platform_tool_ids"]))
        row["rationale"] = (
            "Discovery / retrieval / general tools (BRD-aligned split fallback)."
            if i < n - 1
            else "Persistence: SQL and object storage for interactive use and output contract writes."
        )
    return out
