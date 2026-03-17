from pydantic import BaseModel, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
from models.job import JobStatus


def _parse_int_list(v):
    """Parse optional JSON array string or list to list of ints."""
    if v is None:
        return None
    if isinstance(v, list):
        return [int(x) for x in v if x is not None]
    if isinstance(v, str) and v.strip():
        try:
            out = json.loads(v)
            return [int(x) for x in out] if isinstance(out, list) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return None


# Tool visibility: full | names_only | none (credentials never shared with agents)


class JobCreate(BaseModel):
    title: str
    description: Optional[str] = None
    agent_ids: Optional[List[int]] = []  # For auto-split
    workflow_steps: Optional[List["WorkflowStepCreate"]] = []  # For manual assignment
    allowed_platform_tool_ids: Optional[List[int]] = (
        None  # Tools in scope for this job (empty = all)
    )
    allowed_connection_ids: Optional[List[int]] = (
        None  # MCP connections in scope (empty = all)
    )
    tool_visibility: Optional[str] = None  # full | names_only | none (default full)


class JobUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[JobStatus] = None
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none


class StepToolsAssignment(BaseModel):
    """Per-step tool allowlist for auto-split (by agent index)."""

    agent_index: int  # 0-based index into agent_ids
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none (step override)


class AutoSplitBody(BaseModel):
    """Request body for POST /jobs/{job_id}/workflow/auto-split."""

    agent_ids: List[int] = []
    workflow_mode: Optional[str] = (
        None  # "independent" | "sequential" | None (infer from BRD/conversation)
    )
    step_tools: Optional[List[StepToolsAssignment]] = (
        None  # Which tools each agent (step) can use
    )
    tool_visibility: Optional[str] = None  # Job-level: full | names_only | none


class AnswerQuestionBody(BaseModel):
    """Request body for POST /jobs/{job_id}/answer-question. Accepts 'answer' (preferred) or legacy 'question'."""

    answer: Optional[str] = None
    question: Optional[str] = (
        None  # Legacy: frontend used to send user's answer under this key
    )

    def get_answer(self) -> str:
        """Return the user's answer from either 'answer' or legacy 'question'."""
        return (self.answer or self.question or "").strip()


class WorkflowStepCreate(BaseModel):
    agent_id: int
    step_order: int
    input_data: Optional[Dict[str, Any]] = None
    allowed_platform_tool_ids: Optional[List[int]] = (
        None  # Tools this step can use (empty = job-level)
    )
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none (step override)


class JobResponse(BaseModel):
    id: int
    business_id: int
    title: str
    description: Optional[str]
    status: JobStatus
    total_cost: float
    created_at: datetime
    completed_at: Optional[datetime]
    workflow_steps: Optional[List["WorkflowStepResponse"]] = []
    files: Optional[List[Dict[str, Any]]] = None  # File metadata
    failure_reason: Optional[str] = None  # Reason for job failure
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none

    class Config:
        from_attributes = True

    @field_validator(
        "allowed_platform_tool_ids", "allowed_connection_ids", mode="before"
    )
    @classmethod
    def _coerce_int_list(cls, v):
        return _parse_int_list(v)

    @classmethod
    def model_validate(cls, obj, **kwargs):
        # Parse files JSON string if it exists
        if hasattr(obj, "files") and obj.files:
            try:
                files_data = json.loads(obj.files)
                obj_dict = {k: v for k, v in obj.__dict__.items()}
                obj_dict["files"] = files_data
                # Remove file paths from response for security, only return metadata
                for file_info in files_data:
                    file_info.pop("path", None)
                obj = type(obj)(**obj_dict)
            except (json.JSONDecodeError, TypeError):
                pass
        return super().model_validate(obj, **kwargs)


class WorkflowStepResponse(BaseModel):
    id: int
    job_id: int
    agent_id: int
    agent_name: Optional[str] = None
    step_order: int
    input_data: Optional[str]
    output_data: Optional[str]
    status: str
    cost: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    depends_on_previous: Optional[bool] = (
        True  # False = step works independently (no previous output)
    )
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none

    class Config:
        from_attributes = True

    @field_validator(
        "allowed_platform_tool_ids", "allowed_connection_ids", mode="before"
    )
    @classmethod
    def _coerce_int_list(cls, v):
        return _parse_int_list(v)


class WorkflowPreview(BaseModel):
    steps: List[WorkflowStepResponse]
    total_cost: float
    breakdown: Dict[str, float]  # task_costs, communication_costs, commission


# Update forward references
JobResponse.model_rebuild()
WorkflowStepResponse.model_rebuild()
