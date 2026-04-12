"""
BRD-aware suggestion of which platform MCP tools to assign to each workflow step.

Mirrors task_splitter.split_job_for_agents: same job title, description, BRD excerpts,
and optional Q&A — but output is per-agent platform_tool_ids (subset of the business tool pool).

LLM backend: platform Agent Planner when configured (AGENT_PLANNER_API_KEY); otherwise a deterministic
tool partition fallback (no hired-agent LLM).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from core.config import settings
from models.agent import Agent
from models.mcp_server import MCPToolConfig
from services.planner_llm import is_agent_planner_configured, planner_chat_completion
from services.mcp_tool_capabilities import normalize_tool_type, partition_tools_for_fallback, tool_access_summary

logger = logging.getLogger(__name__)


def _platform_tool_name(tool_id: int, name: str) -> str:
    """Stable MCP tool name (must match api.routes.mcp_internal._tool_name)."""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in (name or "").strip())[:50]
    return f"platform_{tool_id}_{safe}" if safe else f"platform_{tool_id}"


def _tool_catalog_lines(tools: List[MCPToolConfig]) -> str:
    lines = []
    for t in tools:
        tt = normalize_tool_type(t.tool_type.value if hasattr(t.tool_type, "value") else str(t.tool_type))
        s = tool_access_summary(tt)
        lines.append(
            f"- id={t.id} name={t.name!r} type={tt} "
            f"label={s.get('label')} artifact_write={s.get('supports_artifact_platform_write')}"
        )
    return "\n".join(lines)


def _build_write_stub(tools: List[MCPToolConfig]) -> Dict[str, Any]:
    """Minimal write_targets entries for tools that support artifact writes (placeholders for bucket/table)."""
    targets = []
    for t in tools:
        tt = normalize_tool_type(t.tool_type.value if hasattr(t.tool_type, "value") else str(t.tool_type))
        s = tool_access_summary(tt)
        if not s.get("supports_artifact_platform_write"):
            continue
        name = _platform_tool_name(t.id, t.name)
        entry: Dict[str, Any] = {
            "tool_name": name,
            "operation_type": "upsert",
            "write_mode": "overwrite",
            "target": {},
        }
        if tt in ("postgres", "mysql", "sqlserver", "snowflake", "databricks", "bigquery"):
            entry["merge_keys"] = ["<id_column>"]
            entry["target"] = {
                "schema": "public",
                "table": "<your_table>",
            }
            if tt in ("snowflake", "bigquery"):
                entry["target"]["database"] = "<your_database>"
        elif tt in ("s3", "minio", "ceph", "aws_s3"):
            entry["target"] = {"bucket": "<your_bucket>", "prefix": "reports/job-outputs"}
        elif tt == "azure_blob":
            entry["target"] = {"container": "<container>", "prefix": "job-outputs"}
        elif tt == "gcs":
            entry["target"] = {"bucket": "<bucket>", "prefix": "job-outputs"}
        elif tt == "filesystem":
            entry["target"] = {"path": "exports"}
        targets.append(entry)
    return {
        "version": "1.0",
        "write_policy": {"on_write_error": "continue", "min_successful_targets": 1},
        "write_targets": targets,
    }


_NONE_VISIBILITY_RATIONALE = "No platform tools (tool_visibility=none for this step)."


def _effective_step_tool_visibility(
    num_agents: int,
    *,
    step_tool_visibility: Optional[List[Optional[str]]],
    job_tool_visibility: Optional[str],
) -> List[str]:
    job_v = (job_tool_visibility or "full").strip().lower()
    if job_v not in ("full", "names_only", "none"):
        job_v = "full"
    out: List[str] = []
    for i in range(num_agents):
        v: Optional[str] = None
        if step_tool_visibility is not None and i < len(step_tool_visibility):
            raw = step_tool_visibility[i]
            if isinstance(raw, str) and raw.strip():
                cand = raw.strip().lower()
                if cand in ("full", "names_only", "none"):
                    v = cand
        if v is None:
            v = job_v
        out.append(v)
    return out


def _mask_suggestions_for_tool_visibility(
    suggestions: List[Dict[str, Any]],
    effective_vis: List[str],
) -> List[Dict[str, Any]]:
    masked: List[Dict[str, Any]] = []
    for row in suggestions:
        if not isinstance(row, dict):
            masked.append(row)
            continue
        idx = row.get("agent_index")
        r = dict(row)
        if isinstance(idx, int) and 0 <= idx < len(effective_vis) and effective_vis[idx] == "none":
            r["platform_tool_ids"] = []
            r["rationale"] = _NONE_VISIBILITY_RATIONALE
            r.pop("platform_tool_names", None)
        masked.append(r)
    return masked


def _maybe_write_stub(
    platform_tools: List[MCPToolConfig], suggestions: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    for row in suggestions:
        if not isinstance(row, dict):
            continue
        ids = row.get("platform_tool_ids")
        if isinstance(ids, list) and len(ids) > 0:
            return _build_write_stub(platform_tools)
    return None


async def suggest_tool_assignments_for_agents(
    *,
    job_title: str,
    job_description: str,
    documents_content: Optional[List[Dict[str, Any]]],
    conversation_data: Optional[List[Dict[str, Any]]],
    agents: List[Agent],
    platform_tools: List[MCPToolConfig],
    llm_audit: Optional[Dict[str, Any]] = None,
    step_tool_visibility: Optional[List[Optional[str]]] = None,
    job_tool_visibility: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns:
      step_suggestions: list of { agent_index, platform_tool_ids, rationale }
      output_contract_stub: optional dict with write_targets placeholders
      fallback_used: bool
    """
    if not platform_tools:
        return {"step_suggestions": [], "output_contract_stub": None, "fallback_used": True}

    tool_dicts = [
        {"id": t.id, "name": t.name, "tool_type": t.tool_type.value if hasattr(t.tool_type, "value") else str(t.tool_type)}
        for t in platform_tools
    ]

    effective_vis = _effective_step_tool_visibility(
        len(agents),
        step_tool_visibility=step_tool_visibility,
        job_tool_visibility=job_tool_visibility,
    )
    if all(v == "none" for v in effective_vis):
        if llm_audit is not None:
            llm_audit.pop("raw_llm_response", None)
            llm_audit["source"] = "skipped_tool_visibility_none"
            llm_audit["persist_tool_suggestion_without_llm"] = True
        empty_rows = [
            {
                "agent_index": i,
                "agent_name": (agents[i].name or "").strip() if i < len(agents) and getattr(agents[i], "name", None) else None,
                "platform_tool_ids": [],
                "rationale": _NONE_VISIBILITY_RATIONALE,
            }
            for i in range(len(agents))
        ]
        return {
            "step_suggestions": empty_rows,
            "output_contract_stub": None,
            "fallback_used": True,
        }

    use_planner = is_agent_planner_configured()
    if len(agents) == 0 or not use_planner:
        fb = partition_tools_for_fallback(tool_dicts, len(agents))
        for i, row in enumerate(fb):
            if isinstance(row, dict):
                row["agent_name"] = (agents[i].name or "").strip() if i < len(agents) and getattr(agents[i], "name", None) else None
        masked = _mask_suggestions_for_tool_visibility(fb, effective_vis)
        return {
            "step_suggestions": masked,
            "output_contract_stub": _maybe_write_stub(platform_tools, masked),
            "fallback_used": True,
        }

    doc_catalog = documents_content or []
    docs_text = ""
    if doc_catalog:
        docs_text = "\n\n".join(
            f"Document ID: {d.get('id')}\nDocument Name: {d.get('name', 'Unknown')}\n{(d.get('content') or '')[:4000]}"
            for d in doc_catalog
        )

    conv_text = ""
    if conversation_data:
        conv_text = json.dumps(conversation_data, indent=2)[:3000]

    catalog = _tool_catalog_lines(platform_tools)
    agents_desc = "\n".join(
        f"- Agent {i} ({a.name}): {a.description or 'No description'}" for i, a in enumerate(agents)
    )
    valid_ids = [str(t.id) for t in platform_tools]

    system_prompt = """You are a tool planner for a multi-agent workflow platform.

Your job is to assign PLATFORM MCP TOOLS (by numeric id) to each agent step, using the SAME inputs as task splitting:
- job title and description
- BRD / requirement documents
- optional Q&A conversation

RULES:
- Return ONLY valid JSON. No markdown, no explanation.
- Format: [{"agent_index": 0, "platform_tool_ids": [1, 2], "rationale": "short reason"}, ...]
- agent_index must be 0..N-1 for each of the N agents in order.
- Each platform_tool_id MUST be one of the valid ids listed in the catalog.
- Assign READ-HEAVY tools (search, vector, Elasticsearch, PageIndex) to EARLY steps when the job needs research.
- Assign SQL and object-storage tools to steps that need persistence; typically the LAST step owns writes for final results.
- Do not assign a tool to more than one agent unless the job clearly requires it.
- Minimize privilege: fewer tools per step when possible."""

    user_content = f"""JOB TITLE: {job_title}

JOB DESCRIPTION: {job_description or '(none)'}

AGENTS (in order):
{agents_desc}

AVAILABLE PLATFORM TOOLS (assign ONLY these ids: {", ".join(valid_ids)}):
{catalog}

"""
    if docs_text:
        user_content += f"\nBRD / DOCUMENTS:\n{docs_text}\n"
    if conv_text:
        user_content += f"\nQ&A CONVERSATION:\n{conv_text}\n"

    user_content += f"""
Assign tools to each of the {len(agents)} agents. Return JSON array only."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    valid_id_set = {int(x) for x in valid_ids}

    try:
        planner_temperature = float(getattr(settings, "AGENT_PLANNER_TEMPERATURE", 0.3) or 0.3)
        text = await planner_chat_completion(
            messages,
            temperature=planner_temperature,
            max_tokens=min(8192, int(getattr(settings, "AGENT_PLANNER_MAX_TOKENS", 4096) or 4096)),
        )
        if llm_audit is not None:
            llm_audit.pop("persist_tool_suggestion_without_llm", None)
            llm_audit["raw_llm_response"] = text
            llm_audit["source"] = "planner"
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("expected array")

        out: List[Dict[str, Any]] = []
        for i in range(len(agents)):
            entry = next((e for e in parsed if isinstance(e, dict) and e.get("agent_index") == i), None)
            raw_ids = entry.get("platform_tool_ids") if entry else []
            ids: List[int] = []
            if isinstance(raw_ids, list):
                for x in raw_ids:
                    try:
                        xi = int(x)
                    except (TypeError, ValueError):
                        continue
                    if xi in valid_id_set:
                        ids.append(xi)
            ids = list(dict.fromkeys(ids))
            rationale = entry.get("rationale") if entry and isinstance(entry.get("rationale"), str) else ""
            out.append(
                {
                    "agent_index": i,
                    "agent_name": (agents[i].name or "").strip() if getattr(agents[i], "name", None) else None,
                    "platform_tool_ids": ids,
                    "rationale": rationale or "Suggested from BRD and job prompt.",
                }
            )
        # If model returned empty for everyone, fallback (unless every step is visibility none — handled earlier).
        if all(len(x["platform_tool_ids"]) == 0 for x in out):
            raise ValueError("empty assignment")

        masked = _mask_suggestions_for_tool_visibility(out, effective_vis)
        return {
            "step_suggestions": masked,
            "output_contract_stub": _maybe_write_stub(platform_tools, masked),
            "fallback_used": False,
        }
    except Exception as e:
        logger.warning("Tool split LLM failed: %s, using fallback", e)
        fb = partition_tools_for_fallback(tool_dicts, len(agents))
        for i, row in enumerate(fb):
            if isinstance(row, dict):
                row["agent_name"] = (agents[i].name or "").strip() if i < len(agents) and getattr(agents[i], "name", None) else None
        masked = _mask_suggestions_for_tool_visibility(fb, effective_vis)
        return {
            "step_suggestions": masked,
            "output_contract_stub": _maybe_write_stub(platform_tools, masked),
            "fallback_used": True,
        }
