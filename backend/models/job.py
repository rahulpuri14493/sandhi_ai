from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Enum, Text, Boolean
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from db.database import Base


class ScheduleStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class JobStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    IN_QUEUE = "in_queue"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    status = Column(Enum(JobStatus), default=JobStatus.DRAFT)
    total_cost = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    files = Column(Text)  # JSON string storing file metadata: [{"name": "...", "path": "...", "type": "..."}]
    conversation = Column(Text)  # JSON string storing Q&A conversation with AI
    failure_reason = Column(Text, nullable=True)  # Reason for job failure
    # Execution lease token to prevent duplicate/stale worker deliveries.
    execution_token = Column(String(64), nullable=True, index=True)
    # Tool scope for this job: JSON arrays of IDs (empty/null = all business tools)
    allowed_platform_tool_ids = Column(Text, nullable=True)  # e.g. "[1,2]"
    allowed_connection_ids = Column(Text, nullable=True)  # e.g. "[1]"
    # Restrict what tool info agents see: full | names_only | none (credentials never shared)
    tool_visibility = Column(String(20), nullable=True)  # default full
    # platform | agent | ui_only (ui_only: no artifact file, no contract MCP writes; output in DB for UI)
    write_execution_mode = Column(String(20), nullable=False, default="platform")
    # Preferred persisted artifact format for AI outputs: jsonl | json
    output_artifact_format = Column(String(20), nullable=False, default="jsonl")
    # User-defined output contract and write plan (JSON string)
    output_contract = Column(Text, nullable=True)
    # How the current workflow was authored: auto_split (planner/UI auto) vs manual (step-by-step build).
    # Execute-time planner replan skips jobs with workflow_origin=manual to preserve business intent.
    workflow_origin = Column(String(32), nullable=False, default="auto_split")

    # Relationships
    business = relationship("User", back_populates="jobs", foreign_keys=[business_id])
    workflow_steps = relationship("WorkflowStep", back_populates="job", order_by="WorkflowStep.step_order")
    transaction = relationship("Transaction", back_populates="job", uselist=False)
    questions = relationship(
        "JobQuestion",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    # One schedule per job (uselist=False enforces singular access).
    # The DB UNIQUE constraint on job_schedules.job_id prevents duplicates.
    schedule = relationship("JobSchedule", back_populates="job", uselist=False, cascade="all, delete-orphan")
    planner_artifacts = relationship(
        "JobPlannerArtifact",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    input_data = Column(Text)  # JSON string
    output_data = Column(Text)  # JSON string
    status = Column(String, default="pending")  # pending, in_progress, completed, failed
    depends_on_previous = Column(Boolean, default=True, nullable=False)  # False = independent (no previous output)
    cost = Column(Float, default=0.0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    # Durable runtime telemetry snapshot (phase/reason/progress timestamps).
    # Updated on meaningful phase transitions and progress points (not fixed-interval heartbeats).
    last_progress_at = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    live_phase = Column(String(32), nullable=True)
    live_phase_started_at = Column(DateTime, nullable=True)
    live_reason_code = Column(String(64), nullable=True)
    live_reason_detail = Column(Text, nullable=True)
    live_trace_id = Column(String(64), nullable=True)
    live_attempt = Column(Integer, nullable=True)
    stuck_since = Column(DateTime, nullable=True)
    stuck_reason = Column(String(128), nullable=True)
    # Tools this step (agent) can use: JSON arrays (empty/null = use job-level allowed tools)
    allowed_platform_tool_ids = Column(Text, nullable=True)
    allowed_connection_ids = Column(Text, nullable=True)
    # Override job tool_visibility for this step: full | names_only | none
    tool_visibility = Column(String(20), nullable=True)

    # Relationships
    job = relationship("Job", back_populates="workflow_steps")
    agent = relationship("Agent", back_populates="workflow_steps")
    communications_from = relationship("AgentCommunication", foreign_keys="AgentCommunication.from_workflow_step_id", back_populates="from_step")
    communications_to = relationship("AgentCommunication", foreign_keys="AgentCommunication.to_workflow_step_id", back_populates="to_step")


class JobSchedule(Base):
    """One-time schedule for a job. Each job can have at most one schedule (UNIQUE on job_id).

    Workflow:
      1. User creates a schedule with a future scheduled_at datetime.
      2. Celery worker fires an ETA task at that time → job is reset and executed.
      3. After execution, the schedule is deactivated (status=INACTIVE, next_run_time=NULL).
      4. If the job fails, the user can either "Run Now" (POST /rerun) or
         "Schedule Again" (PUT /schedule with a new scheduled_at).
    """
    __tablename__ = "job_schedules"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, unique=True)
    status = Column(
        Enum(
            ScheduleStatus,
            name="schedulestatus",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ScheduleStatus.ACTIVE,
        nullable=False,
    )
    timezone = Column(String, nullable=False, default="UTC")  # IANA timezone e.g. "Asia/Kolkata"
    scheduled_at = Column(DateTime, nullable=False)
    last_run_time = Column(DateTime, nullable=True)
    next_run_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    job = relationship("Job", back_populates="schedule")
    execution_history = relationship("ScheduleExecutionHistory", back_populates="schedule", cascade="all, delete-orphan")


class ScheduleExecutionHistory(Base):
    """Append-only audit log for every schedule execution attempt.

    Statuses: started, completed, failed, skipped, potentially_stuck.
    triggered_by: 'scheduler' (automatic) or 'manual_rerun' (user clicked Run Now).
    """
    __tablename__ = "schedule_execution_history"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("job_schedules.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default="started", nullable=False)  # started, completed, failed, skipped, potentially_stuck
    failure_reason = Column(Text, nullable=True)
    triggered_by = Column(String(50), default="scheduler")  # scheduler, manual_rerun, watchdog

    # Relationships
    schedule = relationship("JobSchedule", back_populates="execution_history")


class JobPlannerArtifact(Base):
    """Pointer to full JSON output from Agent Planner / BRD analysis (object storage or local path)."""

    __tablename__ = "job_planner_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    artifact_type = Column(String(64), nullable=False)  # e.g. brd_analysis
    storage = Column(String(16), nullable=False, default="s3")  # s3 | local
    bucket = Column(String(255), nullable=True)
    object_key = Column(Text, nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    job = relationship("Job", back_populates="planner_artifacts")
