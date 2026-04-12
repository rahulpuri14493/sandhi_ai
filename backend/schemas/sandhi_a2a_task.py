"""
Versioned Sandhi A2A task envelope.

Nested under the executor JSON as ``sandhi_a2a_task`` so external agents get a stable,
machine-readable contract alongside the legacy flat executor fields.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SANDHI_A2A_TASK_SCHEMA_ID = "sandhi.a2a_task.v1"


class AssignedToolMeta(BaseModel):
    """One tool the platform assigned to this task (subset of available_mcp_tools)."""

    model_config = ConfigDict(extra="allow")

    tool_name: str = Field(..., description="OpenAI/MCP function name exposed to the agent")
    platform_tool_id: Optional[int] = None
    external_tool_name: Optional[str] = None
    tool_type: Optional[str] = None
    connection_id: Optional[int] = None
    input_schema: Optional[Dict[str, Any]] = None
    execution_hints: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional hints: timeout bias, read-only, preferred argument shapes, etc.",
    )


class NextAgentRef(BaseModel):
    """Next hop in the workflow (null in ``sandhi_a2a_task`` means terminal step)."""

    model_config = ConfigDict(extra="allow")

    agent_id: int
    workflow_step_id: Optional[int] = None
    name: Optional[str] = None
    a2a_endpoint: Optional[str] = None
    step_order: Optional[int] = None


class ParallelExecutionContext(BaseModel):
    """When ``depends_on_previous=False``, steps may run concurrently within a wave."""

    model_config = ConfigDict(extra="allow")

    wave_index: int = Field(..., ge=0)
    parallel_group_id: str = Field(..., min_length=1)
    concurrent_workflow_step_ids: List[int] = Field(default_factory=list)
    depends_on_previous_wave: bool = True


class SandhiA2ATaskV1(BaseModel):
    """
    Mandatory logical fields for inter-agent handoff (populated by the executor).

    - ``agent_id`` — executing agent (must match root agent and sandhi_trace).
    - ``task_id`` — unique id for this invocation attempt (correlates logs).
    - ``payload`` — minimal task body (mirrors key BRD fields; full docs stay at executor root).
    - ``next_agent`` — following workflow agent, or omitted/null for last step.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: Literal["sandhi.a2a_task.v1"] = SANDHI_A2A_TASK_SCHEMA_ID
    agent_id: int
    task_id: str = Field(..., min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    next_agent: Optional[NextAgentRef] = None
    assigned_tools: List[AssignedToolMeta] = Field(default_factory=list)
    parallel: Optional[ParallelExecutionContext] = None
    task_type: Optional[str] = None
    assignment_source: Optional[str] = Field(
        default=None,
        description="registry | registry_fallback | passthrough | llm",
    )
    assignment_flagged: bool = Field(
        default=False,
        description="True when registry had no match and fallback rules were used.",
    )

    @field_validator("payload", mode="before")
    @classmethod
    def _payload_must_be_object(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        raise TypeError("payload must be an object")


def parse_sandhi_a2a_task(data: Any) -> SandhiA2ATaskV1:
    """Parse and validate a task envelope dict."""
    if not isinstance(data, dict):
        raise ValueError("sandhi_a2a_task must be a JSON object")
    return SandhiA2ATaskV1.model_validate(data)


def task_envelope_to_dict(model: SandhiA2ATaskV1) -> Dict[str, Any]:
    """
    Serialize for the executor JSON body. Always includes:

    - ``next_agent``: ``null`` for the terminal workflow step, or an object (see :class:`NextAgentRef`).
    - ``assigned_tools``: array (possibly empty) of structured tool metadata.

    This matches the published JSON Schema under ``docs/schemas/a2a/sandhi_a2a_task.v1.schema.json`` (required keys).
    """
    d = model.model_dump(mode="python", exclude_none=True)
    if "next_agent" not in d:
        d["next_agent"] = None
    d.setdefault("assigned_tools", [])
    return d
