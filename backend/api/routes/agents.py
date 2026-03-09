from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel
import httpx
from db.database import get_db
from models.agent import Agent, AgentStatus, PricingModel
from models.agent_review import AgentReview
from schemas.agent import AgentCreate, AgentResponse, AgentUpdate
from schemas.agent_review import (
    AgentReviewCreate,
    AgentReviewUpdate,
    AgentReviewResponse,
    AgentReviewSummaryResponse,
    AgentReviewListResponse,
)
from core.security import get_current_user, get_current_user_optional, get_current_developer_user
from core.config import settings
from models.user import User

router = APIRouter(prefix="/api/agents", tags=["agents"])


class TestConnectionRequest(BaseModel):
    api_endpoint: str
    api_key: Optional[str] = None
    test_data: Optional[dict] = None
    llm_model: Optional[str] = None
    temperature: Optional[float] = None
    a2a_enabled: Optional[bool] = False


@router.get("", response_model=List[AgentResponse])
def list_agents(
    response: Response,
    status: Optional[AgentStatus] = Query(None, description="Filter by status"),
    capability: Optional[str] = Query(None, description="Filter by capability"),
    limit: Optional[int] = Query(None, ge=1, le=500, description="Max agents to return (pagination); omit for all"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db)
):
    """List agents (marketplace). Optional limit/offset for scale (e.g. 200+ agents). X-Total-Count header set with total matching count."""
    query = db.query(Agent)

    if status:
        query = query.filter(Agent.status == status)
    else:
        query = query.filter(Agent.status == AgentStatus.ACTIVE)

    if capability:
        query = query.filter(Agent.capabilities.contains([capability]))

    total = query.count()
    if limit is not None:
        query = query.offset(offset).limit(limit)
    agents = query.all()
    agent_ids = [a.id for a in agents]

    # Batch load review counts and averages per agent
    review_stats = (
        db.query(
            AgentReview.agent_id,
            func.count(AgentReview.id).label("count"),
            func.avg(AgentReview.rating).label("avg_rating"),
        )
        .filter(AgentReview.agent_id.in_(agent_ids))
        .group_by(AgentReview.agent_id)
    )
    stats_by_agent = {r.agent_id: (r.count, round(float(r.avg_rating), 2)) for r in review_stats}

    # Exclude api_key from responses for security; include overall rating for marketplace
    result = []
    for agent in agents:
        # Handle pricing_model - default to 'pay_per_use' if None (for existing agents)
        from models.agent import PricingModel
        pricing_model = agent.pricing_model if agent.pricing_model else PricingModel.PAY_PER_USE

        count, avg = stats_by_agent.get(agent.id, (0, 0.0))

        agent_data = {
            "id": agent.id,
            "developer_id": agent.developer_id,
            "name": agent.name,
            "description": agent.description,
            "capabilities": agent.capabilities,
            "input_schema": agent.input_schema,
            "output_schema": agent.output_schema,
            "pricing_model": pricing_model,
            "price_per_task": agent.price_per_task,
            "price_per_communication": agent.price_per_communication,
            "monthly_price": agent.monthly_price,
            "quarterly_price": agent.quarterly_price,
            "api_endpoint": agent.api_endpoint,
            "llm_model": getattr(agent, "llm_model", None),
            "temperature": getattr(agent, "temperature", None),
            "plugin_config": agent.plugin_config,
            "a2a_enabled": getattr(agent, "a2a_enabled", False),
            "status": agent.status,
            "created_at": agent.created_at,
            "average_rating": avg if count else None,
            "review_count": count if count else None,
        }
        result.append(AgentResponse(**agent_data))
    response.headers["X-Total-Count"] = str(total)
    return result


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: int,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get agent by id. Public: no auth required. api_endpoint and api_key only for authenticated users (api_key only for owner)."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    # api_endpoint: only for authenticated users (any logged-in user)
    # api_key: only for the agent owner
    is_owner = current_user is not None and agent.developer_id == current_user.id
    api_endpoint = agent.api_endpoint if current_user is not None else None
    api_key = agent.api_key if is_owner else None
    # Handle None pricing_model for agents created before migration
    pricing_model = agent.pricing_model if agent.pricing_model is not None else PricingModel.PAY_PER_USE
    return AgentResponse(
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
        api_endpoint=api_endpoint,
        api_key=api_key,
        llm_model=getattr(agent, "llm_model", None),
        temperature=getattr(agent, "temperature", None),
        plugin_config=agent.plugin_config,
        a2a_enabled=getattr(agent, "a2a_enabled", False),
        status=agent.status,
        created_at=agent.created_at,
    )


# ---------- Agent reviews (public read, authenticated write) ----------


def _get_agent_or_404(agent_id: int, db: Session) -> Agent:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return agent


@router.get("/{agent_id}/reviews/summary", response_model=AgentReviewSummaryResponse)
def get_agent_reviews_summary(
    agent_id: int,
    db: Session = Depends(get_db),
):
    """Public: overall average rating and count for an agent."""
    _get_agent_or_404(agent_id, db)
    reviews = db.query(AgentReview).filter(AgentReview.agent_id == agent_id).all()
    total = len(reviews)
    if total == 0:
        return AgentReviewSummaryResponse(
            agent_id=agent_id,
            average_rating=0.0,
            total_count=0,
            rating_distribution={1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
        )
    distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in reviews:
        bucket = min(5, max(1, int(round(r.rating))))
        distribution[bucket] = distribution.get(bucket, 0) + 1
    avg = sum(r.rating for r in reviews) / total
    return AgentReviewSummaryResponse(
        agent_id=agent_id,
        average_rating=round(avg, 2),
        total_count=total,
        rating_distribution=distribution,
    )


@router.get("/{agent_id}/reviews", response_model=AgentReviewListResponse)
def list_agent_reviews(
    agent_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Public: paginated list of reviews for an agent."""
    _get_agent_or_404(agent_id, db)
    query = db.query(AgentReview).filter(AgentReview.agent_id == agent_id)
    total = query.count()
    rows = query.order_by(AgentReview.created_at.desc()).offset(offset).limit(limit).all()
    items = [
        AgentReviewResponse(
            id=r.id,
            agent_id=r.agent_id,
            user_id=r.user_id,
            rating=r.rating,
            review_text=r.review_text,
            created_at=r.created_at,
            updated_at=r.updated_at,
            is_own=current_user is not None and r.user_id == current_user.id,
        )
        for r in rows
    ]
    return AgentReviewListResponse(items=items, total=total, limit=limit, offset=offset)


def _get_review_or_404(agent_id: int, review_id: int, db: Session) -> AgentReview:
    review = (
        db.query(AgentReview)
        .filter(AgentReview.id == review_id, AgentReview.agent_id == agent_id)
        .first()
    )
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )
    return review


@router.put("/{agent_id}/reviews/{review_id}", response_model=AgentReviewResponse)
def update_agent_review(
    agent_id: int,
    review_id: int,
    payload: AgentReviewUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a review. Only the review author can update."""
    _get_agent_or_404(agent_id, db)
    review = _get_review_or_404(agent_id, review_id, db)
    if review.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this review",
        )
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(review, k, v)
    review.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(review)
    return AgentReviewResponse(
        id=review.id,
        agent_id=review.agent_id,
        user_id=review.user_id,
        rating=review.rating,
        review_text=review.review_text,
        created_at=review.created_at,
        updated_at=review.updated_at,
        is_own=True,
    )


@router.delete("/{agent_id}/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent_review(
    agent_id: int,
    review_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a review. Only the review author can delete."""
    _get_agent_or_404(agent_id, db)
    review = _get_review_or_404(agent_id, review_id, db)
    if review.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this review",
        )
    db.delete(review)
    db.commit()
    return None


@router.post("/{agent_id}/reviews", response_model=AgentReviewResponse, status_code=status.HTTP_201_CREATED)
def create_agent_review(
    agent_id: int,
    payload: AgentReviewCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new review. Users can submit multiple reviews per agent. Review text is optional."""
    _get_agent_or_404(agent_id, db)
    review_text = (payload.review_text.strip() or None) if payload.review_text else None
    review = AgentReview(
        agent_id=agent_id,
        user_id=current_user.id,
        rating=payload.rating,
        review_text=review_text,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return AgentReviewResponse(
        id=review.id,
        agent_id=review.agent_id,
        user_id=review.user_id,
        rating=review.rating,
        review_text=review.review_text,
        created_at=review.created_at,
        updated_at=getattr(review, "updated_at", None),
        is_own=True,
    )


# ---------- Agent CRUD (authenticated) ----------


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(
    agent_data: AgentCreate,
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    new_agent = Agent(
        developer_id=current_user.id,
        **agent_data.model_dump()
    )
    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)
    # Exclude api_key from response for security
    # Handle None pricing_model for agents created before migration
    pricing_model = new_agent.pricing_model if new_agent.pricing_model is not None else PricingModel.PAY_PER_USE
    return AgentResponse(
        id=new_agent.id,
        developer_id=new_agent.developer_id,
        name=new_agent.name,
        description=new_agent.description,
        capabilities=new_agent.capabilities,
        input_schema=new_agent.input_schema,
        output_schema=new_agent.output_schema,
        pricing_model=pricing_model,
        price_per_task=new_agent.price_per_task,
        price_per_communication=new_agent.price_per_communication,
        monthly_price=new_agent.monthly_price,
        quarterly_price=new_agent.quarterly_price,
        api_endpoint=new_agent.api_endpoint,
        llm_model=getattr(new_agent, "llm_model", None),
        temperature=getattr(new_agent, "temperature", None),
        plugin_config=new_agent.plugin_config,
        a2a_enabled=getattr(new_agent, "a2a_enabled", False),
        status=new_agent.status,
        created_at=new_agent.created_at,
    )


@router.put("/{agent_id}", response_model=AgentResponse)
def update_agent(
    agent_id: int,
    agent_data: AgentUpdate,
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.developer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this agent"
        )
    
    update_data = agent_data.model_dump(exclude_unset=True)
    # If api_key is provided as empty string, don't update it (keep existing)
    if 'api_key' in update_data and update_data['api_key'] == '':
        del update_data['api_key']
    
    for field, value in update_data.items():
        setattr(agent, field, value)
    
    db.commit()
    db.refresh(agent)
    # Include api_key for owner
    # Handle None pricing_model for agents created before migration
    pricing_model = agent.pricing_model if agent.pricing_model is not None else PricingModel.PAY_PER_USE
    return AgentResponse(
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
        api_key=agent.api_key,  # Include api_key for owner
        llm_model=getattr(agent, "llm_model", None),
        temperature=getattr(agent, "temperature", None),
        plugin_config=agent.plugin_config,
        a2a_enabled=getattr(agent, "a2a_enabled", False),
        status=agent.status,
        created_at=agent.created_at,
    )


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(
    agent_id: int,
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.developer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this agent"
        )
    
    # Delete related workflow steps first (to avoid foreign key constraint issues)
    from models.job import WorkflowStep
    from models.communication import AgentCommunication
    from models.transaction import Earnings

    # Get all workflow steps that use this agent
    workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.agent_id == agent_id).all()
    step_ids = [s.id for s in workflow_steps]

    # Unlink earnings from these steps so we can delete the steps
    if step_ids:
        db.query(Earnings).filter(Earnings.workflow_step_id.in_(step_ids)).update(
            {Earnings.workflow_step_id: None}, synchronize_session=False
        )

    # Delete communications related to these workflow steps
    for step in workflow_steps:
        db.query(AgentCommunication).filter(
            (AgentCommunication.from_workflow_step_id == step.id) |
            (AgentCommunication.to_workflow_step_id == step.id)
        ).delete(synchronize_session=False)

    # Delete the workflow steps
    db.query(WorkflowStep).filter(WorkflowStep.agent_id == agent_id).delete(synchronize_session=False)
    
    # Delete communications where this agent is referenced directly
    db.query(AgentCommunication).filter(
        (AgentCommunication.from_agent_id == agent_id) |
        (AgentCommunication.to_agent_id == agent_id)
    ).delete(synchronize_session=False)
    
    # Delete agent nominations if any
    from models.hiring import AgentNomination
    db.query(AgentNomination).filter(AgentNomination.agent_id == agent_id).delete(synchronize_session=False)
    
    # Now delete the agent
    db.delete(agent)
    db.commit()
    return None


@router.get("/{agent_id}/a2a-card", response_model=dict)
def get_agent_a2a_card(
    agent_id: int,
    db: Session = Depends(get_db),
):
    """
    Return an A2A-compliant Agent Card for the agent (discovery).
    See: https://a2a-protocol.org/latest/specification/
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    # Only expose card for agents with an endpoint (can be invoked)
    if not (agent.api_endpoint and (agent.api_endpoint or "").strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent has no API endpoint; cannot produce A2A card",
        )
    # A2A Agent Card (camelCase per spec): identity, capabilities, endpoint, auth requirements
    card = {
        "name": agent.name or f"Agent {agent.id}",
        "description": agent.description or "",
        "capabilities": agent.capabilities if isinstance(agent.capabilities, list) else [],
        "url": agent.api_endpoint.strip(),
        "protocolVersion": "1.0",
        "authentication": {
            "type": "bearer",
            "required": bool(agent.api_key and (agent.api_key or "").strip()),
        },
        "a2aEnabled": getattr(agent, "a2a_enabled", False),
    }
    return card


@router.post("/test-connection", status_code=status.HTTP_200_OK)
async def test_agent_connection(
    test_request: TestConnectionRequest,
    current_user: User = Depends(get_current_developer_user)
):
    """Test connectivity to an agent API endpoint (OpenAI-style or A2A)."""
    if not test_request.api_endpoint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API endpoint is required"
        )
    
    # A2A path: send minimal SendMessage (direct to agent or via platform adapter)
    from services.a2a_client import send_message

    if test_request.a2a_enabled:
        try:
            await send_message(
                test_request.api_endpoint.strip(),
                [{"text": "Connection test from Sandhi AI"}],
                api_key=test_request.api_key,
                blocking=True,
                timeout=10.0,
            )
            return {
                "success": True,
                "message": "A2A connection successful",
                "status_code": 200,
                "response_preview": None,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"A2A connection failed: {str(e)[:200]}",
                "status_code": 500,
                "response_preview": None,
            }

    # OpenAI-compatible: route through platform adapter so test uses same A2A path as execution
    adapter_url = (getattr(settings, "A2A_ADAPTER_URL", None) or "").strip()
    if adapter_url:
        try:
            model = (test_request.llm_model or "").strip() or "gpt-4o-mini"
            await send_message(
                adapter_url,
                [{"text": "Connection test from Sandhi AI"}],
                api_key=None,
                blocking=True,
                timeout=10.0,
                metadata={
                    "openai_url": test_request.api_endpoint.strip(),
                    "openai_api_key": (test_request.api_key or "").strip() or "",
                    "openai_model": model,
                },
            )
            return {
                "success": True,
                "message": "Connection successful (via A2A adapter)",
                "status_code": 200,
                "response_preview": None,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Connection failed (via adapter): {str(e)[:200]}",
                "status_code": 500,
                "response_preview": None,
            }

    headers = {"Content-Type": "application/json"}
    if test_request.api_key:
        headers["Authorization"] = f"Bearer {test_request.api_key}"
    
    # Use custom test data if provided, otherwise try to detect API type and use appropriate payload
    if test_request.test_data:
        test_payload = test_request.test_data
    else:
        # Try to detect API type from endpoint URL
        endpoint_lower = test_request.api_endpoint.lower()
        
        # OpenAI-style API
        if 'openai' in endpoint_lower or '/v1/chat/completions' in endpoint_lower:
            model = (test_request.llm_model or "").strip() or "gpt-4o-mini"
            test_payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": "Hello! This is a connection test."}
                ],
                "max_tokens": 10,
                "temperature": (
                    test_request.temperature
                    if test_request.temperature is not None
                    else 0.7
                ),
            }
        # Anthropic Claude API
        elif 'anthropic' in endpoint_lower or 'claude' in endpoint_lower:
            test_payload = {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 10,
                "messages": [
                    {"role": "user", "content": "Hello! This is a connection test."}
                ]
            }
        # Generic REST API - try a simple test
        else:
            test_payload = {
                "test": True,
                "message": "Connection test from Sandhi AI"
            }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                test_request.api_endpoint,
                json=test_payload,
                headers=headers
            )
            
            # Consider 2xx and 3xx as successful connections
            # Even if the API returns an error, if we got a response, the connection works
            if response.status_code < 400:
                return {
                    "success": True,
                    "message": "Connection successful",
                    "status_code": response.status_code,
                    "response_preview": str(response.text)[:200] if response.text else None
                }
            else:
                # Got a response but with error status - connection works but API rejected
                return {
                    "success": True,
                    "message": f"Connection successful but API returned error (status {response.status_code})",
                    "status_code": response.status_code,
                    "response_preview": str(response.text)[:200] if response.text else None,
                    "warning": "The endpoint is reachable but returned an error. Please verify your API key and request format."
                }
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Connection timeout - the API endpoint did not respond within 10 seconds"
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connection failed - unable to reach the API endpoint. Please check the URL."
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connection error: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error testing connection: {str(e)}"
        )
