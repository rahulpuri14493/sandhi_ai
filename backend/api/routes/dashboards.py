import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from db.database import get_db
from models.user import User
from models.agent import Agent
from models.job import Job, WorkflowStep
from models.transaction import Earnings, EarningsStatus, Transaction, TransactionStatus
from models.communication import AgentCommunication
from schemas.transaction import EarningsResponse
from schemas.job import JobResponse
from core.security import get_current_developer_user, get_current_business_user
from services.execution_heartbeat import get_step_live_state

router = APIRouter(prefix="/api", tags=["dashboards"])


def _parse_output_data(raw: Optional[str]) -> Dict[str, Any]:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_token_usage(output_payload: Dict[str, Any]) -> Dict[str, int]:
    """
    Best-effort token extraction from heterogeneous agent payloads.
    Supports common shapes:
    - agent_output.usage.{prompt_tokens, completion_tokens, total_tokens}
    - agent_output.token_usage.{...}
    - top-level usage/token_usage
    """
    def _coerce_int(v: Any) -> int:
        try:
            return max(0, int(v))
        except Exception:
            return 0

    candidates = []
    if isinstance(output_payload, dict):
        candidates.append(output_payload.get("usage"))
        candidates.append(output_payload.get("token_usage"))
        ao = output_payload.get("agent_output")
        if isinstance(ao, dict):
            candidates.append(ao.get("usage"))
            candidates.append(ao.get("token_usage"))
    for c in candidates:
        if isinstance(c, dict):
            prompt = _coerce_int(c.get("prompt_tokens") or c.get("input_tokens"))
            completion = _coerce_int(c.get("completion_tokens") or c.get("output_tokens"))
            total = _coerce_int(c.get("total_tokens"))
            if total == 0:
                total = prompt + completion
            if prompt or completion or total:
                return {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                }
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _extract_confidence(output_payload: Dict[str, Any]) -> Optional[float]:
    if not isinstance(output_payload, dict):
        return None
    for parent in (output_payload, output_payload.get("agent_output") if isinstance(output_payload.get("agent_output"), dict) else None):
        if isinstance(parent, dict):
            v = parent.get("confidence")
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _safe_ratio(n: float, d: float) -> float:
    if d <= 0:
        return 0.0
    return float(n) / float(d)


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return float(min(values))
    if p >= 100:
        return float(max(values))
    xs = sorted(float(v) for v in values)
    idx = (len(xs) - 1) * (float(p) / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return float(xs[lo] * (1.0 - frac) + xs[hi] * frac)


@router.get("/developers/earnings")
def get_developer_earnings(
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    """Get earnings summary for developer"""
    total_earnings = db.query(func.sum(Earnings.amount)).filter(
        and_(
            Earnings.developer_id == current_user.id,
            Earnings.status == EarningsStatus.PAID
        )
    ).scalar() or 0.0
    
    pending_earnings = db.query(func.sum(Earnings.amount)).filter(
        and_(
            Earnings.developer_id == current_user.id,
            Earnings.status == EarningsStatus.PENDING
        )
    ).scalar() or 0.0
    
    earnings_list = db.query(Earnings).filter(
        Earnings.developer_id == current_user.id
    ).order_by(Earnings.created_at.desc()).limit(50).all()
    
    return {
        "total_earnings": float(total_earnings),
        "pending_earnings": float(pending_earnings),
        "recent_earnings": [EarningsResponse.model_validate(e) for e in earnings_list]
    }


@router.get("/developers/agents")
def get_developer_agents(
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    """List developer's agents - includes api_key since it's their own agents"""
    from schemas.agent import AgentResponse
    agents = db.query(Agent).filter(Agent.developer_id == current_user.id).all()
    # Include api_key for developer's own agents (bypass the model_validate override)
    result = []
    for agent in agents:
        # Create response directly to include api_key for own agents
        # Handle pricing_model - default to 'pay_per_use' if None (for existing agents)
        from models.agent import PricingModel
        pricing_model = agent.pricing_model if agent.pricing_model else PricingModel.PAY_PER_USE
        
        result.append(AgentResponse(
            id=agent.id,
            developer_id=agent.developer_id,
            name=agent.name,
            description=agent.description,
            capabilities=agent.capabilities,
            input_schema=agent.input_schema,
            output_schema=agent.output_schema,
            pricing_model=pricing_model,
            price_per_task=agent.price_per_task,
            price_per_communication=agent.price_per_communication,
            monthly_price=agent.monthly_price,
            quarterly_price=agent.quarterly_price,
            api_endpoint=agent.api_endpoint,
            api_key=agent.api_key,  # Include api_key for own agents
            plugin_config=agent.plugin_config,
            a2a_enabled=getattr(agent, "a2a_enabled", False),
            status=agent.status,
            created_at=agent.created_at,
        ))
    return result


@router.get("/developers/stats")
def get_developer_stats(
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    """Get usage statistics for developer"""
    agent_count = db.query(Agent).filter(Agent.developer_id == current_user.id).count()
    
    # Get total tasks executed
    task_count = db.query(WorkflowStep).join(Agent).filter(
        Agent.developer_id == current_user.id
    ).count()
    
    # Get total communications - need to specify join condition due to multiple foreign keys
    from sqlalchemy import or_
    db.query(Agent.id).filter(Agent.developer_id == current_user.id).subquery()
    comm_count = db.query(AgentCommunication).filter(
        or_(
            AgentCommunication.from_agent_id.in_(db.query(Agent.id).filter(Agent.developer_id == current_user.id)),
            AgentCommunication.to_agent_id.in_(db.query(Agent.id).filter(Agent.developer_id == current_user.id))
        )
    ).count()
    
    return {
        "agent_count": agent_count,
        "total_tasks": task_count,
        "total_communications": comm_count
    }


@router.get("/businesses/jobs")
def get_business_jobs(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """List business jobs"""
    import json
    from schemas.job import WorkflowStepResponse
    jobs = db.query(Job).filter(Job.business_id == current_user.id).all()
    
    # Parse files and conversation for each job
    result = []
    for job in jobs:
        files_data = None
        if job.files:
            try:
                files_parsed = json.loads(job.files)
                # Remove paths for security
                files_data = [{k: v for k, v in f.items() if k != 'path'} for f in files_parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        
        conversation_data = None
        if job.conversation:
            try:
                conversation_data = json.loads(job.conversation)
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Load workflow steps with output data
        workflow_steps_data = []
        workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).order_by(WorkflowStep.step_order).all()
        for step in workflow_steps:
            agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
            workflow_steps_data.append(WorkflowStepResponse(
                id=step.id,
                job_id=step.job_id,
                agent_id=step.agent_id,
                agent_name=agent.name if agent else None,
                step_order=step.step_order,
                input_data=step.input_data,
                output_data=step.output_data,  # Keep as string for frontend to parse
                status=step.status,
                cost=step.cost or 0.0,
                started_at=step.started_at,
                completed_at=step.completed_at,
                depends_on_previous=getattr(step, "depends_on_previous", True),
                allowed_platform_tool_ids=getattr(step, "allowed_platform_tool_ids", None),
                allowed_connection_ids=getattr(step, "allowed_connection_ids", None),
                tool_visibility=getattr(step, "tool_visibility", None),
                live_phase=getattr(step, "live_phase", None),
                live_phase_started_at=getattr(step, "live_phase_started_at", None),
                live_reason_code=getattr(step, "live_reason_code", None),
                live_reason_detail=getattr(step, "live_reason_detail", None),
                live_trace_id=getattr(step, "live_trace_id", None),
                live_attempt=getattr(step, "live_attempt", None),
                last_progress_at=getattr(step, "last_progress_at", None),
                last_activity_at=getattr(step, "last_activity_at", None),
                stuck_since=getattr(step, "stuck_since", None),
                stuck_reason=getattr(step, "stuck_reason", None),
            ))
        
        wo = getattr(job, "workflow_origin", None) or "auto_split"
        if wo not in ("manual", "auto_split"):
            wo = "auto_split"
        job_dict = {
            "id": job.id,
            "business_id": job.business_id,
            "title": job.title,
            "description": job.description,
            "status": job.status,
            "total_cost": job.total_cost,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "workflow_steps": workflow_steps_data,
            "files": files_data,
            "conversation": conversation_data,
            "failure_reason": job.failure_reason,
            "workflow_origin": wo,
            "allowed_platform_tool_ids": getattr(job, "allowed_platform_tool_ids", None),
            "allowed_connection_ids": getattr(job, "allowed_connection_ids", None),
            "tool_visibility": getattr(job, "tool_visibility", None),
        }
        result.append(JobResponse(**job_dict))
    return result


@router.get("/businesses/spending")
def get_business_spending(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Get spending summary for business"""
    total_spent = db.query(func.sum(Transaction.total_amount)).filter(
        and_(
            Transaction.payer_id == current_user.id,
            Transaction.status == TransactionStatus.COMPLETED
        )
    ).scalar() or 0.0
    
    job_count = db.query(Job).filter(Job.business_id == current_user.id).count()
    
    return {
        "total_spent": float(total_spent),
        "job_count": job_count
    }


@router.get("/businesses/agents/performance")
def get_business_agent_performance(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
    limit_steps: int = Query(500, ge=50, le=5000),
):
    """
    End-user agent performance view (hired agents only for this business):
    - usage and cost rollups per agent
    - success/error rates
    - best-effort token usage and confidence proxies
    - latest stage/error from heartbeat (Redis-first with DB fallback)
    """
    steps = (
        db.query(WorkflowStep, Agent)
        .join(Job, Job.id == WorkflowStep.job_id)
        .join(Agent, Agent.id == WorkflowStep.agent_id)
        .filter(Job.business_id == current_user.id)
        .order_by(WorkflowStep.started_at.desc().nullslast(), WorkflowStep.id.desc())
        .limit(limit_steps)
        .all()
    )

    by_agent: Dict[int, Dict[str, Any]] = {}
    now = datetime.utcnow()
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)
    duration_seconds: List[float] = []
    failure_mix: Dict[str, int] = {}
    loop_signals = 0
    drift_signals = 0
    retry_signals = 0
    stuck_steps = 0
    total_cost = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    totals = {"steps": 0, "completed": 0, "failed": 0, "in_progress": 0}
    win7 = {"steps": 0, "completed": 0, "failed": 0, "cost": 0.0}
    win30 = {"steps": 0, "completed": 0, "failed": 0, "cost": 0.0}
    for step, agent in steps:
        totals["steps"] += 1
        total_cost += float(step.cost or 0.0)
        row = by_agent.setdefault(
            agent.id,
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "api_endpoint": agent.api_endpoint,
                "totals": {
                    "steps": 0,
                    "completed_steps": 0,
                    "failed_steps": 0,
                    "in_progress_steps": 0,
                    "cost": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "quality": {
                    "success_rate": 0.0,
                    "average_confidence": None,
                    "confidence_sample_count": 0,
                },
                "latest_runtime": None,
                "recent_failures": [],
            },
        )
        row["totals"]["steps"] += 1
        row["totals"]["cost"] += float(step.cost or 0.0)
        st = (step.status or "").strip().lower()
        if st == "completed":
            row["totals"]["completed_steps"] += 1
            totals["completed"] += 1
        elif st == "failed":
            row["totals"]["failed_steps"] += 1
            totals["failed"] += 1
        elif st == "in_progress":
            row["totals"]["in_progress_steps"] += 1
            totals["in_progress"] += 1

        output_payload = _parse_output_data(step.output_data)
        usage = _extract_token_usage(output_payload)
        row["totals"]["prompt_tokens"] += usage["prompt_tokens"]
        row["totals"]["completion_tokens"] += usage["completion_tokens"]
        row["totals"]["total_tokens"] += usage["total_tokens"]
        total_prompt_tokens += int(usage["prompt_tokens"] or 0)
        total_completion_tokens += int(usage["completion_tokens"] or 0)
        total_tokens += int(usage["total_tokens"] or 0)

        ts_ref = step.completed_at or step.started_at
        if ts_ref is not None:
            if ts_ref >= cutoff_7d:
                win7["steps"] += 1
                win7["cost"] += float(step.cost or 0.0)
                if st == "completed":
                    win7["completed"] += 1
                elif st == "failed":
                    win7["failed"] += 1
            if ts_ref >= cutoff_30d:
                win30["steps"] += 1
                win30["cost"] += float(step.cost or 0.0)
                if st == "completed":
                    win30["completed"] += 1
                elif st == "failed":
                    win30["failed"] += 1

        if step.started_at is not None and step.completed_at is not None:
            dur = (step.completed_at - step.started_at).total_seconds()
            if dur >= 0:
                duration_seconds.append(float(dur))

        reason_raw = (getattr(step, "live_reason_code", None) or "").strip().lower()
        success_like_reasons = {
            "step_completed",
            "completed",
            "done",
            "agent_endpoint_http_ok",
            "platform_write_target_success",
        }
        should_count_failure_reason = (
            st == "failed"
            or getattr(step, "stuck_since", None) is not None
            or ("error" in reason_raw if reason_raw else False)
            or ("failed" in reason_raw if reason_raw else False)
            or ("timeout" in reason_raw if reason_raw else False)
            or ("throttled" in reason_raw if reason_raw else False)
            or ("retry" in reason_raw if reason_raw else False)
            or ("loop" in reason_raw if reason_raw else False)
            or ("drift" in reason_raw if reason_raw else False)
        )
        if st == "failed" and not reason_raw:
            reason_raw = "failed_without_reason"
        if reason_raw and reason_raw in success_like_reasons:
            should_count_failure_reason = False
        if reason_raw and should_count_failure_reason:
            failure_mix[reason_raw] = int(failure_mix.get(reason_raw, 0)) + 1
            if "loop" in reason_raw:
                loop_signals += 1
            if "drift" in reason_raw:
                drift_signals += 1
            if "retry" in reason_raw:
                retry_signals += 1
        if getattr(step, "stuck_since", None) is not None:
            stuck_steps += 1

        conf = _extract_confidence(output_payload)
        if conf is not None:
            cnt = int(row["quality"]["confidence_sample_count"])
            prev_avg = row["quality"]["average_confidence"]
            prev_avg = float(prev_avg) if isinstance(prev_avg, (int, float)) else 0.0
            row["quality"]["average_confidence"] = ((prev_avg * cnt) + conf) / float(cnt + 1)
            row["quality"]["confidence_sample_count"] = cnt + 1

        # Live runtime state for the most recent step of this agent.
        if row["latest_runtime"] is None:
            live = get_step_live_state(job_id=step.job_id, workflow_step_id=step.id)
            db_detail = None
            try:
                db_detail = json.loads(step.live_reason_detail) if step.live_reason_detail else None
            except Exception:
                db_detail = None
            live_detail = (live or {}).get("reason_detail") if isinstance(live, dict) else None
            row["latest_runtime"] = {
                "job_id": step.job_id,
                "workflow_step_id": step.id,
                "step_order": step.step_order,
                "status": step.status,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "last_activity_at": step.last_activity_at.isoformat() if getattr(step, "last_activity_at", None) else None,
                "last_progress_at": step.last_progress_at.isoformat() if getattr(step, "last_progress_at", None) else None,
                "phase": (live or {}).get("phase") if isinstance(live, dict) else getattr(step, "live_phase", None),
                "phase_started_at": (
                    (live or {}).get("phase_started_at")
                    if isinstance(live, dict)
                    else (step.live_phase_started_at.isoformat() if getattr(step, "live_phase_started_at", None) else None)
                ),
                "reason_code": (live or {}).get("reason_code") if isinstance(live, dict) else getattr(step, "live_reason_code", None),
                # Redis live payload is primary, but DB snapshot is a durable fallback.
                "reason_detail": live_detail if isinstance(live_detail, dict) and len(live_detail) > 0 else db_detail,
                "trace_id": (live or {}).get("trace_id") if isinstance(live, dict) else getattr(step, "live_trace_id", None),
                "stuck_since": step.stuck_since.isoformat() if getattr(step, "stuck_since", None) else None,
                "stuck_reason": getattr(step, "stuck_reason", None),
            }

        if st == "failed" and len(row["recent_failures"]) < 5:
            row["recent_failures"].append(
                {
                    "job_id": step.job_id,
                    "workflow_step_id": step.id,
                    "step_order": step.step_order,
                    "failed_at": step.completed_at.isoformat() if step.completed_at else None,
                    "reason_code": getattr(step, "live_reason_code", None),
                    "reason_detail": getattr(step, "live_reason_detail", None),
                }
            )

    # Finalize derived metrics.
    for item in by_agent.values():
        total = max(1, int(item["totals"]["steps"]))
        item["quality"]["success_rate"] = float(item["totals"]["completed_steps"]) / float(total)
        if item["quality"]["average_confidence"] is not None:
            item["quality"]["average_confidence"] = round(float(item["quality"]["average_confidence"]), 4)
        item["totals"]["cost"] = round(float(item["totals"]["cost"]), 6)

    agents_out = sorted(
        by_agent.values(),
        key=lambda x: (x["totals"]["failed_steps"], x["totals"]["steps"]),
        reverse=True,
    )
    failure_mix_top = sorted(
        [{"reason": k, "count": int(v)} for k, v in failure_mix.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:8]

    completed_total = int(totals["completed"])
    failed_total = int(totals["failed"])
    steps_total = int(totals["steps"])
    kpis = {
        "generated_at": now.isoformat() + "Z",
        "overview": {
            "agents": len(agents_out),
            "steps": steps_total,
            "completed_steps": completed_total,
            "failed_steps": failed_total,
            "in_progress_steps": int(totals["in_progress"]),
            "success_rate": round(_safe_ratio(completed_total, max(1, steps_total)), 4),
            "failure_rate": round(_safe_ratio(failed_total, max(1, steps_total)), 4),
            "cost_total": round(float(total_cost), 6),
            "prompt_tokens_total": int(total_prompt_tokens),
            "completion_tokens_total": int(total_completion_tokens),
            "total_tokens": int(total_tokens),
        },
        "latency_seconds": {
            "samples": len(duration_seconds),
            "avg": round(_safe_ratio(sum(duration_seconds), max(1, len(duration_seconds))), 3),
            "p50": round(_percentile(duration_seconds, 50), 3),
            "p95": round(_percentile(duration_seconds, 95), 3),
        },
        "windows": {
            "last_7d": {
                "steps": int(win7["steps"]),
                "completed_steps": int(win7["completed"]),
                "failed_steps": int(win7["failed"]),
                "success_rate": round(_safe_ratio(win7["completed"], max(1, win7["steps"])), 4),
                "cost_total": round(float(win7["cost"]), 6),
            },
            "last_30d": {
                "steps": int(win30["steps"]),
                "completed_steps": int(win30["completed"]),
                "failed_steps": int(win30["failed"]),
                "success_rate": round(_safe_ratio(win30["completed"], max(1, win30["steps"])), 4),
                "cost_total": round(float(win30["cost"]), 6),
            },
        },
        "efficiency": {
            "cost_per_completed_step": round(_safe_ratio(total_cost, max(1, completed_total)), 6),
            "completion_tokens_per_completed_step": round(
                _safe_ratio(total_completion_tokens, max(1, completed_total)), 3
            ),
        },
        "failure_mix": failure_mix_top,
        "risk": {
            "stuck_steps": int(stuck_steps),
            "loop_signals": int(loop_signals),
            "drift_signals": int(drift_signals),
            "retry_signals": int(retry_signals),
        },
    }
    return {
        "business_id": current_user.id,
        "sampled_steps": len(steps),
        "agents": agents_out,
        "kpis": kpis,
    }
