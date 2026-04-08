"""
Internal execution telemetry API for ops/admin screens.

Provides merged step live state:
- Redis hot state (when available)
- Durable DB fallback snapshot
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.config import settings
from db.database import get_db
from models.job import Job, JobStatus, WorkflowStep
from services.execution_heartbeat import get_step_live_state, publish_step_heartbeat
from services.heartbeat_signature import (
    derive_execution_hmac_key,
    heartbeat_body_sha256,
    heartbeat_signing_string,
    now_epoch_s,
    secure_equals_hex,
    sign_heartbeat_string,
)

router = APIRouter(prefix="/api/internal/execution", tags=["execution-internal"])

INTERNAL_SECRET_HEADER = "x-internal-secret"
HB_VERSION_HEADER = "x-heartbeat-version"
HB_KEY_ID_HEADER = "x-heartbeat-key-id"
HB_TS_HEADER = "x-heartbeat-timestamp"
HB_NONCE_HEADER = "x-heartbeat-nonce"
HB_SIG_HEADER = "x-heartbeat-signature"
HB_EXECUTION_TOKEN_HEADER = "x-execution-token"


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


def _get_replay_redis():
    # Reuse heartbeat Redis channel for anti-replay nonce keys.
    try:
        from services.execution_heartbeat import _get_redis_client  # pylint: disable=import-outside-toplevel

        return _get_redis_client()
    except Exception:
        return None


def _nonce_key(*, job_id: int, step_id: int, nonce: str) -> str:
    return f"sandhi:heartbeat_nonce:v1:{int(job_id)}:{int(step_id)}:{nonce}"


class HeartbeatIngestRequest(BaseModel):
    schema_version: Optional[str] = None
    phase: str = Field(..., min_length=1, max_length=32)
    reason_code: str = Field(..., min_length=1, max_length=64)
    message: Optional[str] = Field(None, max_length=240)
    reason_detail: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = Field(None, max_length=64)
    attempt: Optional[int] = Field(None, ge=0, le=1000)
    max_retries: Optional[int] = Field(None, ge=0, le=1000)
    meaningful_progress: bool = False


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


@router.post("/jobs/{job_id}/steps/{step_id}/heartbeat")
def internal_ingest_step_heartbeat(
    job_id: int,
    step_id: int,
    body: HeartbeatIngestRequest,
    _: str = Depends(_verify_internal_secret),
    x_heartbeat_version: Optional[str] = Header(None, alias=HB_VERSION_HEADER),
    x_heartbeat_key_id: Optional[str] = Header(None, alias=HB_KEY_ID_HEADER),
    x_heartbeat_timestamp: Optional[str] = Header(None, alias=HB_TS_HEADER),
    x_heartbeat_nonce: Optional[str] = Header(None, alias=HB_NONCE_HEADER),
    x_heartbeat_signature: Optional[str] = Header(None, alias=HB_SIG_HEADER),
    x_execution_token: Optional[str] = Header(None, alias=HB_EXECUTION_TOKEN_HEADER),
    db: Session = Depends(get_db),
):
    if not bool(getattr(settings, "HEARTBEAT_SIGNED_API_ENABLED", True)):
        raise HTTPException(status_code=503, detail="Signed heartbeat API is disabled")
    version = (x_heartbeat_version or "").strip()
    expected_version = (getattr(settings, "HEARTBEAT_SIGNED_API_VERSION", None) or "sandhi.heartbeat.v1").strip()
    if version != expected_version:
        raise HTTPException(status_code=400, detail="Unsupported heartbeat contract version")
    if (x_heartbeat_key_id or "").strip() != "exec_token_v1":
        raise HTTPException(status_code=400, detail="Unsupported heartbeat key id")
    nonce = (x_heartbeat_nonce or "").strip()
    if not nonce or len(nonce) > 128:
        raise HTTPException(status_code=400, detail="Invalid heartbeat nonce")
    sig = (x_heartbeat_signature or "").strip()
    if not sig:
        raise HTTPException(status_code=400, detail="Missing heartbeat signature")
    try:
        ts = int((x_heartbeat_timestamp or "").strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid heartbeat timestamp")
    skew = max(5, int(getattr(settings, "HEARTBEAT_SIGNED_API_SKEW_SECONDS", 120) or 120))
    if abs(now_epoch_s() - ts) > skew:
        raise HTTPException(status_code=401, detail="Heartbeat timestamp outside allowed skew window")

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
    job_token = (getattr(job, "execution_token", None) or "").strip()
    req_token = (x_execution_token or "").strip()
    if (
        not job_token
        or not req_token
        or req_token != job_token
        or (getattr(job, "status", None) != JobStatus.IN_PROGRESS)
    ):
        raise HTTPException(status_code=403, detail="Invalid execution scope for heartbeat")

    # Canonicalize only caller-provided fields to keep signing stable across defaults.
    body_dict = body.model_dump(exclude_none=True, exclude_unset=True)
    body_hash = heartbeat_body_sha256(body_dict)
    path = f"/api/internal/execution/jobs/{int(job_id)}/steps/{int(step_id)}/heartbeat"
    signing_str = heartbeat_signing_string(
        method="POST",
        route_path=path,
        version=version,
        key_id="exec_token_v1",
        timestamp=ts,
        nonce=nonce,
        body_sha256=body_hash,
        job_id=job_id,
        workflow_step_id=step_id,
    )
    key = derive_execution_hmac_key(job_id=job_id, execution_token=job_token)
    expected_sig = sign_heartbeat_string(key=key, signing_string=signing_str)
    if not secure_equals_hex(sig, expected_sig):
        raise HTTPException(status_code=403, detail="Invalid heartbeat signature")

    r = _get_replay_redis()
    nonce_ttl = max(30, int(getattr(settings, "HEARTBEAT_NONCE_TTL_SECONDS", 300) or 300))
    if r is not None:
        try:
            ok = r.set(_nonce_key(job_id=job_id, step_id=step_id, nonce=nonce), b"1", ex=nonce_ttl, nx=True)
            if not ok:
                raise HTTPException(status_code=409, detail="Duplicate heartbeat nonce")
        except HTTPException:
            raise
        except Exception:
            # Fail closed when anti-replay backend is unhealthy.
            raise HTTPException(status_code=503, detail="Heartbeat replay guard unavailable")

    publish_step_heartbeat(
        db=db,
        step=step,
        phase=body.phase,
        reason_code=body.reason_code,
        message=body.message,
        reason_detail=body.reason_detail,
        trace_id=body.trace_id,
        attempt=body.attempt,
        max_retries=body.max_retries,
        execution_token=req_token,
        meaningful_progress=bool(body.meaningful_progress),
        commit_db=True,
    )
    return {
        "accepted": True,
        "job_id": int(job_id),
        "workflow_step_id": int(step_id),
        "contract_version": version,
        "server_ts": datetime.utcnow().isoformat() + "Z",
    }
