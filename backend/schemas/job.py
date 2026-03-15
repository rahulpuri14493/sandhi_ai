from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import re
from zoneinfo import available_timezones
from models.job import JobStatus, ScheduleStatus


_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_VALID_TIMEZONES = available_timezones()


def _validate_timezone(tz: str) -> str:
    if tz not in _VALID_TIMEZONES:
        raise ValueError(f"Invalid timezone: {tz}")
    return tz


def _validate_time_str(t: str) -> str:
    if not _TIME_PATTERN.match(t):
        raise ValueError("Time must be in HH:MM format (e.g. 09:00)")
    return t


def build_cron_from_schedule(days_of_week: List[int], time: str) -> str:
    """Convert day list + time to a 5-field cron expression.
    e.g. [1,3,5] + "09:30" → "30 9 * * 1,3,5"
    """
    parts = time.split(":")
    hour, minute = parts[0].lstrip("0") or "0", parts[1].lstrip("0") or "0"
    dow = ",".join(str(d) for d in sorted(days_of_week)) if days_of_week else "*"
    return f"{minute} {hour} * * {dow}"


def build_cron_from_datetime(dt: datetime) -> str:
    """Convert a datetime to a cron expression matching that exact minute.
    e.g. Mar 20 14:30 → "30 14 20 3 *"
    """
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"


class JobScheduleCreate(BaseModel):
    is_one_time: bool
    timezone: str = "UTC"
    scheduled_at: Optional[datetime] = None  # Required for one-time
    days_of_week: Optional[List[int]] = None  # Required for recurring (0=Sun, 6=Sat)
    time: Optional[str] = None  # Required for recurring, "HH:MM"
    status: ScheduleStatus = ScheduleStatus.ACTIVE

    @field_validator("timezone")
    @classmethod
    def validate_tz(cls, v: str) -> str:
        return _validate_timezone(v)

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_time_str(v)
        return v

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None:
            for d in v:
                if d < 0 or d > 6:
                    raise ValueError("days_of_week values must be 0-6 (Sun=0, Sat=6)")
            if not v:
                raise ValueError("days_of_week must not be empty")
        return v

    @model_validator(mode="after")
    def check_required_fields(self):
        if self.is_one_time:
            if self.scheduled_at is None:
                raise ValueError("scheduled_at is required for one-time schedules")
        else:
            if self.days_of_week is None:
                raise ValueError("days_of_week is required for recurring schedules")
            if self.time is None:
                raise ValueError("time is required for recurring schedules")
        return self


class JobScheduleUpdate(BaseModel):
    is_one_time: Optional[bool] = None
    timezone: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    days_of_week: Optional[List[int]] = None
    time: Optional[str] = None
    status: Optional[ScheduleStatus] = None

    @field_validator("timezone")
    @classmethod
    def validate_tz(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_timezone(v)
        return v

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_time_str(v)
        return v

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None:
            for d in v:
                if d < 0 or d > 6:
                    raise ValueError("days_of_week values must be 0-6 (Sun=0, Sat=6)")
        return v


class JobScheduleResponse(BaseModel):
    id: int
    job_id: int
    status: ScheduleStatus
    is_one_time: bool
    timezone: str
    scheduled_at: Optional[datetime] = None
    days_of_week: Optional[List[int]] = None
    time: Optional[str] = None
    last_run_time: Optional[datetime] = None
    next_run_time: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_validator("days_of_week", mode="before")
    @classmethod
    def parse_days_of_week(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip():
            return [int(d) for d in v.split(",") if d.strip()]
        if isinstance(v, list):
            return [int(d) for d in v]
        return None

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """Map DB column 'schedule_time' to response field 'time'."""
        if hasattr(obj, "schedule_time") and not hasattr(obj, "time"):
            # Create a dict copy to remap the field
            data = {c.key: getattr(obj, c.key) for c in obj.__table__.columns}
            data["time"] = data.pop("schedule_time", None)
            data.pop("cron_expression", None)  # Internal field, not in response
            return super().model_validate(data, **kwargs)
        return super().model_validate(obj, **kwargs)


class JobScheduleWithJobResponse(JobScheduleResponse):
    """Schedule response enriched with job title for cross-job listing."""
    job_title: str = ""


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
    workflow_mode: Optional[str] = None  # "independent" | "sequential" | None (infer from BRD/conversation)
    step_tools: Optional[List[StepToolsAssignment]] = None  # Which tools each agent (step) can use
    tool_visibility: Optional[str] = None  # Job-level: full | names_only | none


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


# Update forward references
JobResponse.model_rebuild()
WorkflowStepResponse.model_rebuild()
JobScheduleResponse.model_rebuild()
