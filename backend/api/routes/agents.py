from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import httpx
from db.database import get_db
from models.agent import Agent, AgentStatus, PricingModel
from schemas.agent import AgentCreate, AgentResponse, AgentUpdate
from core.security import get_current_user, get_current_developer_user
from models.user import User

router = APIRouter(prefix="/api/agents", tags=["agents"])


class TestConnectionRequest(BaseModel):
    api_endpoint: str
    api_key: Optional[str] = None
    test_data: Optional[dict] = None
    llm_model: Optional[str] = None
    temperature: Optional[float] = None


@router.get("", response_model=List[AgentResponse])
def list_agents(
    status: Optional[AgentStatus] = Query(None, description="Filter by status"),
    capability: Optional[str] = Query(None, description="Filter by capability"),
    db: Session = Depends(get_db)
):
    query = db.query(Agent)
    
    if status:
        query = query.filter(Agent.status == status)
    else:
        query = query.filter(Agent.status == AgentStatus.ACTIVE)
    
    if capability:
        query = query.filter(Agent.capabilities.contains([capability]))
    
    agents = query.all()
    # Exclude api_key from responses for security
    result = []
    for agent in agents:
        # Handle pricing_model - default to 'pay_per_use' if None (for existing agents)
        from models.agent import PricingModel
        pricing_model = agent.pricing_model if agent.pricing_model else PricingModel.PAY_PER_USE
        
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
            "status": agent.status,
            "created_at": agent.created_at,
        }
        result.append(AgentResponse(**agent_data))
    return result


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    # Include api_key only if user is the agent owner
    is_owner = agent.developer_id == current_user.id
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
        api_key=agent.api_key if is_owner else None,  # Include api_key only for owner
        llm_model=getattr(agent, "llm_model", None),
        temperature=getattr(agent, "temperature", None),
        plugin_config=agent.plugin_config,
        status=agent.status,
        created_at=agent.created_at,
    )


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
    
    # Get all workflow steps that use this agent
    workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.agent_id == agent_id).all()
    
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


@router.post("/test-connection", status_code=status.HTTP_200_OK)
async def test_agent_connection(
    test_request: TestConnectionRequest,
    current_user: User = Depends(get_current_developer_user)
):
    """Test connectivity to an agent API endpoint"""
    if not test_request.api_endpoint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API endpoint is required"
        )
    
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
