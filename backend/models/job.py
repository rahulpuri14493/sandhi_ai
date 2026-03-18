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
    # Tool scope for this job: JSON arrays of IDs (empty/null = all business tools)
    allowed_platform_tool_ids = Column(Text, nullable=True)  # e.g. "[1,2]"
    allowed_connection_ids = Column(Text, nullable=True)  # e.g. "[1]"
    # Restrict what tool info agents see: full | names_only | none (credentials never shared)
    tool_visibility = Column(String(20), nullable=True)  # default full

    # Relationships
    business = relationship("User", back_populates="jobs", foreign_keys=[business_id])
    workflow_steps = relationship("WorkflowStep", back_populates="job", order_by="WorkflowStep.step_order")
    transaction = relationship("Transaction", back_populates="job", uselist=False)
    questions = relationship(
        "JobQuestion",
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
    __tablename__ = "job_schedules"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    cron_expression = Column(String, nullable=False)  # Generated internally, never shown to users
    status = Column(Enum(ScheduleStatus, name="schedulestatus"), default=ScheduleStatus.ACTIVE, nullable=False)
    is_one_time = Column(Boolean, default=False, nullable=False)
    timezone = Column(String, nullable=False, default="UTC")  # IANA timezone e.g. "Asia/Kolkata"
    scheduled_at = Column(DateTime(timezone=True), nullable=True)  # One-time only: when to run
    days_of_week = Column(String, nullable=True)  # Recurring: comma-separated day numbers "1,3,5"
    schedule_time = Column(String, nullable=True)  # Recurring: "HH:MM" e.g. "09:00"
    last_run_time = Column(DateTime, nullable=True)
    next_run_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    job = relationship("Job", back_populates="schedules")
