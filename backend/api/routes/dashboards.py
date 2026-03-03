from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import Dict, Any
from db.database import get_db
from models.user import User, UserRole
from models.agent import Agent
from models.job import Job, WorkflowStep
from models.transaction import Earnings, EarningsStatus, Transaction, TransactionStatus
from models.communication import AgentCommunication
from schemas.transaction import EarningsResponse
from schemas.job import JobResponse
from core.security import get_current_user, get_current_developer_user, get_current_business_user

router = APIRouter(prefix="/api", tags=["dashboards"])


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
    developer_agent_subquery = db.query(Agent.id).filter(Agent.developer_id == current_user.id).subquery()
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
                completed_at=step.completed_at
            ))
        
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
            "failure_reason": job.failure_reason
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
