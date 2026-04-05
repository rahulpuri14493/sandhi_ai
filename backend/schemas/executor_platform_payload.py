"""
Standard Sandhi executor → agent (A2A / OpenAI / adapter) payload contract.

Every outbound execution payload is validated (optional, on by default) and enriched with:
- ``platform_a2a_schema``: stable identifier for support and external agent implementers
- ``sandhi_trace``: correlation IDs for logs and postmortems (job, step, agent)

Unknown keys from WorkflowStep.input_data are preserved (``extra='allow'``) so the workflow
builder can extend the contract without code changes. Tools remain optional: empty or missing
``available_mcp_tools`` is valid.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

logger = logging.getLogger(__name__)

# Stable ID for logs, agents, and docs (bump only for breaking payload shape changes)
EXECUTOR_CONTEXT_SCHEMA_ID = "sandhi.executor_context.v1"


class ExecutorAgentPayload(BaseModel):
    """
    Known fields the platform executor sets or reads. Additional keys are allowed
    for forward compatibility (e.g. custom BRD fields).
    """

    model_config = ConfigDict(extra="allow")

    job_title: Optional[str] = None
    job_description: Optional[str] = None
    documents: Optional[List[Any]] = None
    conversation: Optional[List[Any]] = None
    assigned_task: Optional[str] = None
    agent_name: Optional[str] = None
    agent_description: Optional[str] = None
    step_order: Optional[int] = None
    total_steps: Optional[int] = None
    previous_step_output: Optional[Any] = None
    available_mcp_tools: Optional[List[Any]] = None
    business_id: Optional[int] = None
    peer_agents: Optional[List[Any]] = None
    output_contract: Optional[Dict[str, Any]] = None
    write_execution_mode: Optional[str] = None
    write_targets: Optional[List[Any]] = None
    document_scope_restricted: Optional[bool] = None
    allowed_document_ids: Optional[List[Any]] = None
    assigned_document_names: Optional[List[Any]] = None
    # Structured tool assignment + versioned A2A task envelope (optional for legacy jobs)
    assigned_tools: Optional[List[Any]] = None
    sandhi_a2a_task: Optional[Dict[str, Any]] = None

    @field_validator("assigned_tools", mode="before")
    @classmethod
    def _assigned_tools_list_of_objects(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError(f"assigned_tools must be a list or null, got {type(v).__name__}")
        for i, item in enumerate(v):
            if not isinstance(item, dict):
                raise ValueError(f"assigned_tools[{i}] must be an object")
        return v

    @field_validator("sandhi_a2a_task", mode="before")
    @classmethod
    def _sandhi_a2a_task_object(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError(f"sandhi_a2a_task must be an object or null, got {type(v).__name__}")
        return v

    @field_validator("documents", mode="before")
    @classmethod
    def _documents_must_be_list_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        raise ValueError(f"documents must be a list or null, got {type(v).__name__}")

    @field_validator("conversation", mode="before")
    @classmethod
    def _conversation_must_be_list_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        raise ValueError(f"conversation must be a list or null, got {type(v).__name__}")

    @field_validator("available_mcp_tools", mode="before")
    @classmethod
    def _tools_list_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        raise ValueError(f"available_mcp_tools must be a list or null, got {type(v).__name__}")

    @field_validator("peer_agents", mode="before")
    @classmethod
    def _peers_list_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        raise ValueError(f"peer_agents must be a list or null, got {type(v).__name__}")

    @field_validator("write_targets", mode="before")
    @classmethod
    def _write_targets_list_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        raise ValueError(f"write_targets must be a list or null, got {type(v).__name__}")

    @field_validator("output_contract", mode="before")
    @classmethod
    def _contract_dict_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        raise ValueError(f"output_contract must be an object or null, got {type(v).__name__}")


def validate_and_enrich_executor_payload(
    input_data: Dict[str, Any],
    *,
    job_id: int,
    workflow_step_id: int,
    step_order: int,
    agent_id: int,
    total_steps: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Validate well-known types, then attach schema id + trace for every agent call (A2A wire uses JSON body).

    Raises:
        ValueError: with job/step context if validation fails (logged at ERROR first).
    """
    try:
        model = ExecutorAgentPayload.model_validate(input_data)
    except ValidationError as e:
        err_summary = e.errors()
        logger.error(
            "executor_payload_validation_failed job_id=%s workflow_step_id=%s step_order=%s agent_id=%s errors=%s",
            job_id,
            workflow_step_id,
            step_order,
            agent_id,
            err_summary,
        )
        raise ValueError(
            f"Invalid executor payload for A2A/agent (job_id={job_id} workflow_step_id={workflow_step_id} "
            f"step_order={step_order} agent_id={agent_id}): {err_summary}"
        ) from e

    # Omit nulls on the wire to keep A2A JSON lean under high load; extras and set fields remain.
    out = model.model_dump(mode="python", exclude_none=True)

    out["platform_a2a_schema"] = EXECUTOR_CONTEXT_SCHEMA_ID
    out["sandhi_trace"] = {
        "job_id": job_id,
        "workflow_step_id": workflow_step_id,
        "step_order": step_order,
        "agent_id": agent_id,
        "total_steps": total_steps,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    return out


def enrich_executor_payload_trace_only(
    input_data: Dict[str, Any],
    *,
    job_id: int,
    workflow_step_id: int,
    step_order: int,
    agent_id: int,
    total_steps: Optional[int] = None,
) -> Dict[str, Any]:
    """Skip Pydantic validation; only add schema marker + trace (escape hatch)."""
    out = dict(input_data)
    out["platform_a2a_schema"] = EXECUTOR_CONTEXT_SCHEMA_ID
    out["sandhi_trace"] = {
        "job_id": job_id,
        "workflow_step_id": workflow_step_id,
        "step_order": step_order,
        "agent_id": agent_id,
        "total_steps": total_steps,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    return out
