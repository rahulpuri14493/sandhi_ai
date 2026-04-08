"""
Internal execution telemetry API for ops/admin screens.

Provides merged step live state:
- Redis hot state (when available)
- Durable DB fallback snapshot
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from core.config import settings
from db.database import get_db
from models.job import Job, WorkflowStep
from services.execution_heartbeat import get_step_live_state

router = APIRouter(prefix="/api/internal/execution", tags=["execution-internal"])

INTERNAL_SECRET_HEADER = "x-internal-secret"


def _verify_internal_secret(
    x_internal_secret: Optional[str] = Header(None, alias=INTERNAL_SECRET_HEADER),
) -> str:
    secret = (getattr(settings, "MCP_INTERNAL_SECRET", None) or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Internal execution API not configured")
    if x_internal_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid or missing internal secret")
    return x_internal_secret or ""


def _try_parse_json_obj(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw or not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt.startswith("{"):
        return None
    try:
        data = json.loads(txt)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _step_live_row(step: WorkflowStep, *, job_id: int, compact: bool) -> Dict[str, Any]:
    redis_live = get_step_live_state(job_id=job_id, workflow_step_id=step.id)
    db_reason_detail = _try_parse_json_obj(getattr(step, "live_reason_detail", None))
    fallback_live = {
        "phase": getattr(step, "live_phase", None),
        "phase_started_at": (
            step.live_phase_started_at.isoformat() if getattr(step, "live_phase_started_at", None) else None
        ),
        "reason_code": getattr(step, "live_reason_code", None),
        "reason_detail": db_reason_detail,
        "trace_id": getattr(step, "live_trace_id", None),
        "attempt": getattr(step, "live_attempt", None),
        "last_progress_ts": (
            step.last_progress_at.isoformat() if getattr(step, "last_progress_at", None) else None
        ),
        "last_activity_ts": (
            step.last_activity_at.isoformat() if getattr(step, "last_activity_at", None) else None
        ),
        "stuck_since": (
            step.stuck_since.isoformat() if getattr(step, "stuck_since", None) else None
        ),
        "stuck_reason": getattr(step, "stuck_reason", None),
    }
    if not compact:
        fallback_live["reason_detail_json"] = getattr(step, "live_reason_detail", None)

    live = redis_live if isinstance(redis_live, dict) else fallback_live
    if compact and isinstance(live, dict):
        # Compact mode keeps high-signal fields for heavy admin tables and polling.
        detail = live.get("reason_detail")
        if not isinstance(detail, dict):
            detail = None
        live = {
            "phase": live.get("phase"),
            "phase_started_at": live.get("phase_started_at"),
            "reason_code": live.get("reason_code"),
            "reason_detail": detail,
            "trace_id": live.get("trace_id"),
            "attempt": live.get("attempt"),
            "last_progress_ts": live.get("last_progress_ts"),
            "last_activity_ts": live.get("last_activity_ts"),
            "stuck_since": live.get("stuck_since"),
            "stuck_reason": live.get("stuck_reason"),
        }
    return {
        "workflow_step_id": step.id,
        "step_order": step.step_order,
        "agent_id": step.agent_id,
        "status": step.status,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        "live": live,
        "live_source": "redis" if isinstance(redis_live, dict) else "db_fallback",
    }


@router.get("/jobs/{job_id}/steps/live")
def internal_get_job_steps_live_state(
    job_id: int,
    compact: bool = Query(True, description="Compact response for admin list polling (default true)"),
    _: str = Depends(_verify_internal_secret),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    steps = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.job_id == job_id)
        .order_by(WorkflowStep.step_order)
        .all()
    )

    out_steps = [_step_live_row(step, job_id=job_id, compact=compact) for step in steps]

    return {
        "job_id": job.id,
        "job_status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "execution_token": getattr(job, "execution_token", None),
        "steps": out_steps,
    }


@router.get("/jobs/{job_id}/steps/{step_id}/live")
def internal_get_one_step_live_state(
    job_id: int,
    step_id: int,
    compact: bool = Query(False, description="Compact response for lightweight polling"),
    _: str = Depends(_verify_internal_secret),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    step = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.job_id == job_id, WorkflowStep.id == step_id)
        .first()
    )
    if not step:
        raise HTTPException(status_code=404, detail="Workflow step not found")

    return {
        "job_id": job.id,
        "job_status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "execution_token": getattr(job, "execution_token", None),
        "step": _step_live_row(step, job_id=job_id, compact=compact),
    }
