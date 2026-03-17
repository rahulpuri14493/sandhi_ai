"""Service to split a job into subtasks for multiple agents (generalized, no hardcoding)."""

import logging
import json
import httpx
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
) -> List[Dict[str, str]]:
    """
    Use the first agent's API to split the job into N subtasks, one per agent.
    Returns list of {"agent_index": int, "task": str} for each agent.
    Falls back to equal-share if split fails.
    """
    if len(agents) <= 1:
        return [
            {
                "agent_index": 0,
                "task": _build_full_task_context(
                    job_title, job_description, documents_content
                ),
            }
        ]

    url = (splitter_agent.api_endpoint or "").strip()
    if not url:
        return _fallback_tasks(agents, job_title, job_description, documents_content)

    # Build context for the splitter (BRD = documents; use larger excerpt so division is based on full requirements)
    agents_desc = "\n".join(
        f"- Agent {i} ({a.name}): {a.description or 'No description'}"
        for i, a in enumerate(agents)
    )
    docs_text = ""
    if documents_content:
        # Include more BRD content so work division is driven by requirements (up to ~5000 chars per doc)
        docs_text = "\n\n".join(
            f"Document: {d.get('name', 'Unknown')}\n{d.get('content', '')[:5000]}"
            for d in documents_content
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
- Format: [{"agent_index": 0, "task": "..."}, {"agent_index": 1, "task": "..."}, ...]
- agent_index must be 0-based (0, 1, 2, ...) for each of the N agents.
- Derive each subtask directly from the BRD and the job prompt. Each task must reference the specific requirement or scope it fulfils.
- Each task must be SELF-CONTAINED and SCOPE-BOUND: each agent does ONLY its part, nothing else.
- CRITICAL: Each task must explicitly state what the agent must NOT do (e.g. "Do NOT perform subtraction" for an addition-only agent).
- For sequential workflows: Agent 0 does the first step; Agent 1 receives "the result from the previous agent" and does the next step; etc.
- Each task must say "Return ONLY [specific output]" so the agent does not over-execute.
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

    user_content += f"""Using the BRD, job prompt, and Q&A above, split this job into {len(agents)} subtasks. Return JSON array with agent_index and task for each agent."""

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
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                return _fallback_tasks(
                    agents, job_title, job_description, documents_content
                )

            data = resp.json()
            text = (
                data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            ).strip()
            # Remove markdown code blocks if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = json.loads(text)
            if isinstance(parsed, list) and len(parsed) >= len(agents):
                # Ensure we have one entry per agent
                result = []
                for i in range(len(agents)):
                    entry = next((e for e in parsed if e.get("agent_index") == i), None)
                    if entry and isinstance(entry.get("task"), str):
                        result.append({"agent_index": i, "task": entry["task"]})
                    else:
                        result.append(
                            {
                                "agent_index": i,
                                "task": _build_agent_task_fallback(
                                    agents[i],
                                    job_title,
                                    job_description,
                                    i,
                                    len(agents),
                                ),
                            }
                        )
                return result
    except Exception as e:
        logger.warning("Task split failed: %s, using fallback", e)

    return _fallback_tasks(agents, job_title, job_description, documents_content)


def _fallback_tasks(
    agents: List[Agent],
    job_title: str,
    job_description: str,
    documents_content: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Fallback when LLM split fails: each agent gets full context + role hint."""
    return [
        {
            "agent_index": i,
            "task": _build_agent_task_fallback(
                a, job_title, job_description, i, len(agents)
            ),
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
    base += f"Execute ONLY the part of the job that matches your role. "
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
