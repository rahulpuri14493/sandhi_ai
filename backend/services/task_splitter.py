"""Service to split a job into subtasks for multiple agents (generalized, no hardcoding)."""
import logging
import json
import re
from core.config import settings
from services.llm_http_client import post_openai_compatible_raw
from services.planner_llm import is_agent_planner_configured, planner_chat_completion
from typing import List, Dict, Any, Optional
from models.agent import Agent

logger = logging.getLogger(__name__)


async def split_job_for_agents(
    job_title: str,
    job_description: str,
    documents_content: List[Dict[str, Any]],
    conversation_data: Optional[List[Dict]],
    agents: List[Agent],
    splitter_agent: Agent,
    llm_audit: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Split the job into N subtasks, one per agent.

    LLM backend: when the platform Agent Planner is configured (AGENT_PLANNER_API_KEY set and enabled),
    completions use that model. Otherwise the first workflow agent's OpenAI-compatible endpoint is used
    (same credentials/model fields as that agent). If neither is available, returns fallback tasks.

    Returns list of {"agent_index": int, "task": str, "assigned_document_ids": [..]?} for each agent.

    If llm_audit is a dict, it is filled with raw_llm_response and source when an LLM response
    was received (before JSON parse), for persisting planner artifacts.
    """
    if len(agents) <= 1:
        all_doc_ids = [d.get("id") for d in documents_content if d.get("id")]
        return [{
            "agent_index": 0,
            "task": _build_full_task_context(job_title, job_description, documents_content),
            "assigned_document_ids": all_doc_ids if all_doc_ids else None,
        }]

    doc_catalog = _build_document_catalog(documents_content)

    use_planner = is_agent_planner_configured()
    url = (splitter_agent.api_endpoint or "").strip()
    if not use_planner and not url:
        return _fallback_tasks(agents, job_title, job_description, documents_content)

    # Build context for the splitter (BRD = documents; use larger excerpt so division is based on full requirements)
    agents_desc = "\n".join(
        f"- Agent {i} ({a.name}): {a.description or 'No description'}"
        for i, a in enumerate(agents)
    )
    docs_text = ""
    if doc_catalog:
        # Include more BRD content so work division is driven by requirements (up to ~5000 chars per doc)
        docs_text = "\n\n".join(
            f"Document ID: {d.get('id')}\nDocument Name: {d.get('name', 'Unknown')}\n{d.get('content', '')[:5000]}"
            for d in doc_catalog
        )
    conv_text = ""
    if conversation_data:
        # Q&A from analyze-documents (BRD-driven); use for refining how work is divided
        conv_text = json.dumps(conversation_data, indent=2)[:3000]

    system_prompt = """You are a task planner for a multi-agent platform. Your job is to divide work among N agents.

WORK DIVISION MUST BE DRIVEN BY:
1. The BRD (Business Requirements Documents) – requirements, scope, and criteria from the uploaded documents.
2. The job prompt – job title and description provided by the user.
3. The Q&A conversation (when present) – questions asked by the AI based on the BRD and the user's answers. Use these to refine requirements before splitting.

RULES:
- Return ONLY valid JSON. No markdown, no explanation.
- Format: [{"agent_index": 0, "task": "...", "assigned_document_ids": ["BRD1"]}, {"agent_index": 1, "task": "...", "assigned_document_ids": ["BRD2"]}, ...]
- agent_index must be 0-based (0, 1, 2, ...) for each of the N agents.
- Derive each subtask directly from the BRD and the job prompt. Each task must reference the specific requirement or scope it fulfils.
- Each task must be SELF-CONTAINED and SCOPE-BOUND: each agent does ONLY its part, nothing else.
- CRITICAL: Each task must explicitly state what the agent must NOT do (e.g. "Do NOT perform subtraction" for an addition-only agent).
- For sequential workflows: Agent 0 does the first step; Agent 1 receives "the result from the previous agent" and does the next step; etc.
- Each task must say "Return ONLY [specific output]" so the agent does not over-execute.
- CRITICAL: Set assigned_document_ids for each agent. Use only IDs listed in the BRD catalog.
- Split the work fairly; each agent gets one clear, bounded subtask based on the BRD and prompt."""

    user_content = f"""JOB TITLE (user prompt): {job_title}

JOB DESCRIPTION (user prompt): {job_description or '(none)'}

AGENTS (each will perform one subtask):
{agents_desc}

"""
    if docs_text:
        user_content += f"""BRD / REQUIREMENT DOCUMENTS (use these to divide work; requirements here drive the split):
{docs_text}

"""
    if conv_text:
        user_content += f"""Q&A FROM BRD (questions asked by AI based on documents; user answers – use to refine requirements when splitting):
{conv_text}

"""

    user_content += f"""Using the BRD, job prompt, and Q&A above, split this job into {len(agents)} subtasks.
Return JSON array with agent_index, task, and assigned_document_ids for each agent.
If user text explicitly says mappings like "BRD1 handled by Agent1", enforce them strictly in assigned_document_ids."""

    model = (getattr(splitter_agent, "llm_model", None) or "").strip() or "gpt-4o-mini"
    temperature = (
        getattr(splitter_agent, "temperature", None)
        if getattr(splitter_agent, "temperature", None) is not None
        else 0.3
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }

    headers = {"Content-Type": "application/json"}
    if splitter_agent.api_key and (splitter_agent.api_key or "").strip():
        headers["Authorization"] = f"Bearer {(splitter_agent.api_key or '').strip()}"

    try:
        if use_planner:
            text = await planner_chat_completion(
                payload["messages"],
                temperature=temperature,
                max_tokens=min(8192, int(getattr(settings, "AGENT_PLANNER_MAX_TOKENS", 4096) or 4096)),
            )
        else:
            fb = (getattr(settings, "LLM_HTTP_FALLBACK_MODEL", None) or "").strip() or None
            resp = await post_openai_compatible_raw(
                url, headers, payload, timeout=60.0, max_retries=2, fallback_model=fb
            )
            if resp.status_code >= 400:
                return _fallback_tasks(agents, job_title, job_description, documents_content)

            data = resp.json()
            text = (
                data.get("choices", [{}])[0].get("message", {}).get("content", "")
                or ""
            ).strip()
        if llm_audit is not None:
            llm_audit["raw_llm_response"] = text
            llm_audit["source"] = "planner" if use_planner else "agent_endpoint"
        # Remove markdown code blocks if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        parsed = json.loads(text)
        if isinstance(parsed, list) and len(parsed) >= len(agents):
            explicit_map = _extract_explicit_document_agent_mapping(job_description, doc_catalog, agents)
            has_model_scope = any(isinstance(e.get("assigned_document_ids"), list) and len(e.get("assigned_document_ids")) > 0 for e in parsed if isinstance(e, dict))
            strict_scope = bool(explicit_map) or has_model_scope
            normalized_scope = _normalize_agent_document_scope(
                parsed_assignments=parsed,
                explicit_assignments=explicit_map,
                doc_catalog=doc_catalog,
                agents=agents,
                strict_scope=strict_scope,
            )
            # Ensure we have one entry per agent
            result = []
            for i in range(len(agents)):
                entry = next((e for e in parsed if e.get("agent_index") == i), None)
                if entry and isinstance(entry.get("task"), str):
                    result.append({
                        "agent_index": i,
                        "task": entry["task"],
                        "assigned_document_ids": normalized_scope.get(i),
                    })
                else:
                    result.append({
                        "agent_index": i,
                        "task": _build_agent_task_fallback(
                            agents[i], job_title, job_description, i, len(agents)
                        ),
                        "assigned_document_ids": normalized_scope.get(i),
                    })
            return result
    except Exception as e:
        logger.warning("Task split failed: %s, using fallback", e)

    return _fallback_tasks(agents, job_title, job_description, documents_content)


def _fallback_tasks(
    agents: List[Agent],
    job_title: str,
    job_description: str,
    documents_content: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fallback when LLM split fails: each agent gets full context + role hint."""
    return [
        {
            "agent_index": i,
            "task": _build_agent_task_fallback(
                a, job_title, job_description, i, len(agents)
            ),
            "assigned_document_ids": None,
        }
        for i, a in enumerate(agents)
    ]


def _build_agent_task_fallback(
    agent: Agent,
    job_title: str,
    job_description: str,
    index: int,
    total: int,
) -> str:
    """Build a generic task when we cannot use LLM split."""
    base = f"You are agent {index + 1} of {total}. "
    if agent.description:
        base += f"Your expertise: {agent.description}. "
    base += "Execute ONLY the part of the job that matches your role. "
    if total > 1 and index > 0:
        base += "You will receive the previous agent's output. Use it as your input. "
    base += f"Do NOT perform work assigned to other agents. Return ONLY your specific output. Job: {job_title}. {job_description or ''}"
    return base


def _build_full_task_context(
    job_title: str,
    job_description: str,
    documents_content: List[Dict[str, Any]],
) -> str:
    """Full task for single-agent job."""
    parts = [f"Job: {job_title}. {job_description or ''}"]
    if documents_content:
        for d in documents_content:
            parts.append(f"Document {d.get('name', '')}: {d.get('content', '')[:1500]}")
    return " ".join(parts)


def _build_document_catalog(documents_content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []
    for idx, d in enumerate(documents_content or []):
        doc_id = str(d.get("id") or f"BRD{idx + 1}")
        catalog.append({
            "id": doc_id,
            "name": d.get("name", f"document_{idx + 1}"),
            "content": d.get("content", ""),
        })
    return catalog


def _extract_explicit_document_agent_mapping(
    job_description: str,
    doc_catalog: List[Dict[str, Any]],
    agents: List[Agent],
) -> Dict[int, List[str]]:
    """
    Parse explicit user mapping from prompt text, e.g.:
    - "BRD1 handled by agent1"
    - "addition document -> Agent 1"
    """
    text = (job_description or "").lower()
    if not text or not doc_catalog:
        return {}
    clauses = [c.strip() for c in re.split(r"[\n;]|\.(?=\s|$)|\band\b", text) if c.strip()]
    mapping: Dict[int, List[str]] = {}

    agent_tokens: Dict[int, List[str]] = {}
    for idx, agent in enumerate(agents):
        name = (getattr(agent, "name", "") or "").strip().lower()
        tokens = [f"agent{idx + 1}", f"agent {idx + 1}"]
        if name:
            tokens.append(name)
        agent_tokens[idx] = tokens

    agent_patterns: Dict[int, List[str]] = {
        idx: [_bounded_token_regex(t) for t in toks if t]
        for idx, toks in agent_tokens.items()
    }

    for d_idx, doc in enumerate(doc_catalog):
        doc_id = str(doc.get("id", f"BRD{d_idx + 1}"))
        doc_name = str(doc.get("name", "")).lower()
        stem = re.sub(r"\.[a-z0-9]+$", "", doc_name)
        aliases = {doc_id.lower(), f"brd{d_idx + 1}"}
        if stem:
            aliases.add(stem)
        if doc_name:
            aliases.add(doc_name)
        doc_patterns = [_bounded_token_regex(a) for a in aliases if a]

        for clause in clauses:
            for agent_idx, a_patterns in agent_patterns.items():
                if _has_explicit_pair_match(clause, doc_patterns, a_patterns):
                    mapping.setdefault(agent_idx, [])
                    if doc_id not in mapping[agent_idx]:
                        mapping[agent_idx].append(doc_id)
    return mapping


def _bounded_token_regex(token: str) -> str:
    # Word boundaries avoid collisions like BRD1 vs BRD10 and agent1 vs agent10.
    return rf"(?<![a-z0-9]){re.escape(token.lower())}(?![a-z0-9])"


def _has_explicit_pair_match(text: str, doc_patterns: List[str], agent_patterns: List[str]) -> bool:
    if not text or not doc_patterns or not agent_patterns:
        return False
    connectors = r"(?:handled\s+by|handle\s+by|handled|handle|assigned\s+to|owned\s+by|by|->|to)"
    window = r".{0,60}?"
    for d in doc_patterns:
        for a in agent_patterns:
            # document -> connector -> agent
            p1 = rf"{d}{window}{connectors}{window}{a}"
            # agent -> connector -> document
            p2 = rf"{a}{window}{connectors}{window}{d}"
            if re.search(p1, text, flags=re.IGNORECASE | re.DOTALL) or re.search(
                p2, text, flags=re.IGNORECASE | re.DOTALL
            ):
                return True
    return False


def _normalize_agent_document_scope(
    parsed_assignments: List[Dict[str, Any]],
    explicit_assignments: Dict[int, List[str]],
    doc_catalog: List[Dict[str, Any]],
    agents: List[Agent],
    strict_scope: bool,
) -> Dict[int, Optional[List[str]]]:
    valid_ids_ordered = [str(d["id"]) for d in doc_catalog if d.get("id")]
    valid_ids = set(valid_ids_ordered)
    if not strict_scope or not valid_ids_ordered:
        return {i: None for i in range(len(agents))}

    scope_map: Dict[int, List[str]] = {}
    # Explicit user mapping takes precedence.
    for idx in range(len(agents)):
        ids = explicit_assignments.get(idx)
        if ids:
            scope_map[idx] = [x for x in ids if str(x) in valid_ids]

    # Fill from model output where explicit mapping is absent.
    for entry in parsed_assignments:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("agent_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(agents):
            continue
        if idx in scope_map:
            continue
        ids = entry.get("assigned_document_ids")
        if isinstance(ids, list):
            cleaned = [str(x) for x in ids if str(x) in valid_ids]
            if cleaned:
                scope_map[idx] = cleaned

    # Ensure each agent gets at least one document under strict mode.
    assigned = {doc_id for ids in scope_map.values() for doc_id in ids}
    remaining = [doc_id for doc_id in valid_ids_ordered if doc_id not in assigned]
    for idx in range(len(agents)):
        if idx in scope_map:
            continue
        if remaining:
            scope_map[idx] = [remaining.pop(0)]
        else:
            # If all docs already assigned, keep this agent unrestricted to avoid empty input.
            scope_map[idx] = list(valid_ids_ordered)
    return {i: scope_map.get(i) for i in range(len(agents))}
