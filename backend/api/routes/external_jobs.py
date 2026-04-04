"""
External Job API - allows end users and external systems to interact with jobs outside the platform.

Authentication:
- View job: JWT token (from share link) in query ?token=xxx or header X-Job-Token
- Create job: X-API-Key header (must match EXTERNAL_API_KEY)
"""
from typing import Optional
import json
from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session
from pydantic import BaseModel

from db.database import get_db
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from models.user import User, UserRole
from schemas.job import WorkflowStepResponse
from core.config import settings
from core.external_token import verify_job_token

router = APIRouter(prefix="/api/external/jobs", tags=["external-jobs"])

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _verify_job_token(token: str, job_id: int) -> bool:
    """Verify JWT token for job access."""
    return verify_job_token(token, job_id)


def _get_external_api_key() -> Optional[str]:
    key = getattr(settings, "EXTERNAL_API_KEY", "") or ""
    return key.strip() if key else None


async def _verify_external_api_key(api_key: Optional[str] = Depends(api_key_header)) -> bool:
    """Verify X-API-Key for external job creation."""
    expected = _get_external_api_key()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External API is not configured. Set EXTERNAL_API_KEY in environment.",
        )
    if not api_key or api_key.strip() != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Use X-API-Key header.",
        )
    return True


def _verify_job_token_for_request(job_id: int, token: Optional[str], x_job_token: Optional[str]) -> None:
    """Verify token for job access (query param or header)."""
    t = token or x_job_token
    if not _verify_job_token(t or "", job_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired job token. Get a share link from the job owner.",
        )


def _build_job_response(job: Job, db: Session) -> dict:
    """Build job response with workflow steps and agent names."""
    files_data = None
    if job.files:
        try:
            parsed = json.loads(job.files)
            files_data = [{k: v for k, v in f.items() if k != "path"} for f in parsed]
        except (json.JSONDecodeError, TypeError):
            pass

    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass

    workflow_steps_data = []
    steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).order_by(WorkflowStep.step_order).all()
    for step in steps:
        agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
        workflow_steps_data.append(
            WorkflowStepResponse(
                id=step.id,
                job_id=step.job_id,
                agent_id=step.agent_id,
                agent_name=agent.name if agent else None,
                step_order=step.step_order,
                input_data=step.input_data,
                output_data=step.output_data,
                status=step.status,
                cost=step.cost or 0.0,
                started_at=step.started_at,
                completed_at=step.completed_at,
                depends_on_previous=getattr(step, "depends_on_previous", True),
            )
        )

    return {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status.value if hasattr(job.status, "value") else job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": workflow_steps_data,
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": job.failure_reason,
    }


# --- External endpoints (no platform login required) ---


@router.get("/{job_id}", response_model=dict)
def get_job_external(
    job_id: int,
    token: Optional[str] = None,
    x_job_token: Optional[str] = Header(None, alias="X-Job-Token"),
    db: Session = Depends(get_db),
):
    """
    Get job details and results (for end users outside the platform).
    Requires a valid job token from the share link.
    Token can be passed as query param ?token=xxx or header X-Job-Token.
    """
    _verify_job_token_for_request(job_id, token, x_job_token)
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _build_job_response(job, db)


@router.get("/{job_id}/status", response_model=dict)
def get_job_status_external(
    job_id: int,
    token: Optional[str] = None,
    x_job_token: Optional[str] = Header(None, alias="X-Job-Token"),
    db: Session = Depends(get_db),
):
    """
    Get job status and agent outputs (lightweight, for polling).
    Requires a valid job token from the share link.
    """
    _verify_job_token_for_request(job_id, token, x_job_token)
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status.value if hasattr(job.status, "value") else job.status,
        "failure_reason": job.failure_reason,
        "workflow_steps": [
            {
                "step_order": s.step_order,
                "agent_id": s.agent_id,
                "status": s.status,
                "output_data": s.output_data,
            }
            for s in db.query(WorkflowStep)
            .filter(WorkflowStep.job_id == job_id)
            .order_by(WorkflowStep.step_order)
            .all()
        ],
    }


class ExternalJobCreate(BaseModel):
    title: str
    description: Optional[str] = None


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_job_external(
    body: ExternalJobCreate,
    _: bool = Depends(_verify_external_api_key),
    db: Session = Depends(get_db),
):
    """
    Create a job from an external system (no platform login).
    Requires X-API-Key header matching EXTERNAL_API_KEY.
    Jobs are created under the first business user (or configure EXTERNAL_BUSINESS_USER_ID).
    """
    business = db.query(User).filter(User.role == UserRole.BUSINESS).first()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No business user configured. Create a business account in the platform first.",
        )

    job = Job(
        business_id=business.id,
        title=body.title,
        description=body.description,
        status=JobStatus.DRAFT,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    from core.external_token import create_job_token, get_share_url
    token = create_job_token(job.id)
    share_url = get_share_url(job.id)

    return {
        "id": job.id,
        "title": job.title,
        "status": job.status.value,
        "share_url": share_url,
        "token": token,
        "message": "Job created. Use share_url or token to access job status and results.",
    }
