from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone as tz
import json
from zoneinfo import available_timezones
from models.job import JobStatus, ScheduleStatus


_VALID_TIMEZONES = available_timezones()


def _validate_timezone(v: str) -> str:
    """Validate that the timezone string is a valid IANA timezone (e.g. 'Asia/Kolkata')."""
    if v not in _VALID_TIMEZONES:
        raise ValueError(f"Invalid timezone: {v}")
    return v


def _is_in_past(v: datetime) -> bool:
    """Check if a datetime is in the past. Handles both naive and tz-aware inputs.

    Uses datetime.utcnow() for consistency with the rest of the codebase.
    Tz-aware inputs are converted to UTC before comparison; naive inputs
    are assumed to be UTC.
    """
    now = datetime.utcnow()
    if v.tzinfo:
        # Convert tz-aware input to naive UTC for comparison
        compare = v.astimezone(tz.utc).replace(tzinfo=None)
    else:
        compare = v
    return compare < now


class JobScheduleCreate(BaseModel):
    """Schema for creating a one-time job schedule.

    scheduled_at must be a future datetime. timezone is the IANA timezone
    the user intended (used by APScheduler's DateTrigger).
    """
    scheduled_at: datetime
    timezone: str = "UTC"
    status: ScheduleStatus = ScheduleStatus.ACTIVE

    @field_validator("timezone")
    @classmethod
    def validate_tz(cls, v: str) -> str:
        return _validate_timezone(v)

    @field_validator("scheduled_at")
    @classmethod
    def validate_not_in_past(cls, v: datetime) -> datetime:
        if _is_in_past(v):
            raise ValueError("scheduled_at must be in the future")
        return v


class JobScheduleUpdate(BaseModel):
    """Schema for updating a schedule. All fields optional.

    Used for both regular updates and "Schedule Again" after job failure
    (frontend sends a new scheduled_at and optionally status=active).
    """
    scheduled_at: Optional[datetime] = None
    timezone: Optional[str] = None
    status: Optional[ScheduleStatus] = None

    @field_validator("timezone")
    @classmethod
    def validate_tz(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_timezone(v)
        return v

    @field_validator("scheduled_at")
    @classmethod
    def validate_not_in_past(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and _is_in_past(v):
            raise ValueError("scheduled_at must be in the future")
        return v


class JobScheduleResponse(BaseModel):
    id: int
    job_id: int
    status: ScheduleStatus
    timezone: str
    scheduled_at: datetime
    last_run_time: Optional[datetime] = None
    next_run_time: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class JobScheduleWithJobResponse(JobScheduleResponse):
    """Schedule response enriched with job title and status for cross-job listing."""
    job_title: str = ""
    job_status: str = ""


class ScheduleListResponse(BaseModel):
    """Paginated list of schedules."""
    items: List[JobScheduleWithJobResponse]
    total: int
    limit: int
    offset: int


class ScheduleExecutionHistoryResponse(BaseModel):
    id: int
    schedule_id: int
    job_id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    failure_reason: Optional[str] = None
    triggered_by: str

    class Config:
        from_attributes = True


class EnumOption(BaseModel):
    """Single enum value for dynamic filter dropdowns."""
    value: str
    label: str


class JobFilterOptions(BaseModel):
    """All available filter values for the job list endpoint."""
    statuses: List[EnumOption]
    sort_options: List[EnumOption]


class ScheduleFilterOptions(BaseModel):
    """All available filter values for the schedule list endpoint."""
    schedule_statuses: List[EnumOption]
    job_statuses: List[EnumOption]
    sort_options: List[EnumOption]
    jobs: List[dict]  # [{id, title}] — user's jobs for the job_id dropdown


class ScheduleActionResponse(BaseModel):
    """Standard response for schedule create/update mutations."""
    message: str
    data: JobScheduleResponse


class RerunResponse(BaseModel):
    """Response for job rerun operation."""
    message: str
    job_id: int
    status: str


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
    allowed_platform_tool_ids: Optional[List[int]] = None  # Tools in scope for this job (empty = all)
    allowed_connection_ids: Optional[List[int]] = None  # MCP connections in scope (empty = all)
    tool_visibility: Optional[str] = None  # full | names_only | none (default full)
    write_execution_mode: Optional[str] = "platform"  # platform | agent | ui_only
    output_artifact_format: Optional[str] = "jsonl"  # jsonl | json
    output_contract: Optional[Dict[str, Any]] = None  # universal output contract


class JobUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[JobStatus] = None
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none
    write_execution_mode: Optional[str] = None
    output_artifact_format: Optional[str] = None
    output_contract: Optional[Dict[str, Any]] = None


class StepToolsAssignment(BaseModel):
    """Per-step tool allowlist for auto-split (by agent index)."""
    agent_index: int  # 0-based index into agent_ids
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none (step override)


class AutoSplitBody(BaseModel):
    """Request body for POST /jobs/{job_id}/workflow/auto-split."""
    agent_ids: List[int] = []
    workflow_mode: Optional[str] = None  # "independent" | "sequential" | None (infer from BRD/conversation)
    step_tools: Optional[List[StepToolsAssignment]] = None  # Which tools each agent (step) can use
    tool_visibility: Optional[str] = None  # Job-level: full | names_only | none
    write_execution_mode: Optional[str] = None  # platform | agent | ui_only
    output_artifact_format: Optional[str] = None  # jsonl | json
    output_contract: Optional[Dict[str, Any]] = None


class AnswerQuestionBody(BaseModel):
    """Request body for POST /jobs/{job_id}/answer-question. Accepts 'answer' (preferred) or legacy 'question'."""
    answer: Optional[str] = None
    question: Optional[str] = None  # Legacy: frontend used to send user's answer under this key

    def get_answer(self) -> str:
        """Return the user's answer from either 'answer' or legacy 'question'."""
        return (self.answer or self.question or "").strip()


class WorkflowStepCreate(BaseModel):
    agent_id: int
    step_order: int
    input_data: Optional[Dict[str, Any]] = None
    allowed_platform_tool_ids: Optional[List[int]] = None  # Tools this step can use (empty = job-level)
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
    write_execution_mode: Optional[str] = "platform"  # platform | agent | ui_only
    output_artifact_format: Optional[str] = "jsonl"  # jsonl | json
    output_contract: Optional[Dict[str, Any]] = None
    # Schedule-aware fields for frontend UX
    show_cancel_option: bool = False  # True when in_progress job exceeds stuck threshold
    scheduled_at: Optional[datetime] = None  # From job's schedule, for countdown timer

    class Config:
        from_attributes = True

    @field_validator("allowed_platform_tool_ids", "allowed_connection_ids", mode="before")
    @classmethod
    def _coerce_int_list(cls, v):
        return _parse_int_list(v)

    @classmethod
    def model_validate(cls, obj, **kwargs):
        # Parse files JSON string if it exists
        if hasattr(obj, 'files') and obj.files:
            try:
                files_data = json.loads(obj.files)
                obj_dict = {k: v for k, v in obj.__dict__.items()}
                obj_dict['files'] = files_data
                # Remove storage internals from response for security, only return metadata
                for file_info in files_data:
                    file_info.pop('path', None)
                    file_info.pop('bucket', None)
                    file_info.pop('key', None)
                    file_info.pop('storage', None)
                obj = type(obj)(**obj_dict)
            except (json.JSONDecodeError, TypeError):
                pass
        if hasattr(obj, 'output_contract') and isinstance(getattr(obj, 'output_contract', None), str):
            try:
                contract_data = json.loads(obj.output_contract)
                obj_dict = {k: v for k, v in obj.__dict__.items()}
                obj_dict['output_contract'] = contract_data
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
    depends_on_previous: Optional[bool] = True  # False = step works independently (no previous output)
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none

    class Config:
        from_attributes = True

    @field_validator("allowed_platform_tool_ids", "allowed_connection_ids", mode="before")
    @classmethod
    def _coerce_int_list(cls, v):
        return _parse_int_list(v)


class WorkflowPreview(BaseModel):
    steps: List[WorkflowStepResponse]
    total_cost: float
    breakdown: Dict[str, float]  # task_costs, communication_costs, commission


class PlannerArtifactResponse(BaseModel):
    """Postgres pointer to planner JSON in MinIO/S3 (or local dev path). object_key is not redacted — job owner only."""

    id: int
    job_id: int
    artifact_type: str
    storage: str
    bucket: Optional[str] = None
    object_key: str
    byte_size: int
    created_at: datetime

    class Config:
        from_attributes = True


class PlannerArtifactListResponse(BaseModel):
    items: List[PlannerArtifactResponse]


class PlannerPipelineBundleResponse(BaseModel):
    """
    Read model: latest persisted brd_analysis, task_split, and tool_suggestion JSON per job.
    Does not change how artifacts are written; composes existing storage pointers.
    """

    schema_version: str = "planner_pipeline.v1"
    job_id: int
    brd_analysis: Optional[Dict[str, Any]] = None
    task_split: Optional[Dict[str, Any]] = None
    tool_suggestion: Optional[Dict[str, Any]] = None
    artifact_ids: Dict[str, Optional[int]] = Field(default_factory=dict)


# Update forward references
JobResponse.model_rebuild()
WorkflowStepResponse.model_rebuild()
JobScheduleResponse.model_rebuild()
