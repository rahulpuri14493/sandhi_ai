from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from db.database import get_db
from models.hiring import HiringPosition, AgentNomination, HiringStatus, NominationStatus
from models.user import User, UserRole
from models.agent import Agent
from schemas.hiring import (
    HiringPositionCreate,
    HiringPositionUpdate,
    HiringPositionResponse,
    AgentNominationCreate,
    AgentNominationUpdate,
    AgentNominationResponse,
    HiringPositionWithNominations
)
from core.security import get_current_user, get_current_business_user, get_current_developer_user

router = APIRouter(prefix="/api/hiring", tags=["hiring"])


@router.post("/positions", response_model=HiringPositionResponse, status_code=status.HTTP_201_CREATED)
def create_hiring_position(
    position_data: HiringPositionCreate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Create a new hiring position (business users only)"""
    new_position = HiringPosition(
        business_id=current_user.id,
        **position_data.model_dump()
    )
    db.add(new_position)
    db.commit()
    db.refresh(new_position)
    
    return HiringPositionResponse(
        id=new_position.id,
        business_id=new_position.business_id,
        title=new_position.title,
        description=new_position.description,
        requirements=new_position.requirements,
        status=new_position.status,
        created_at=new_position.created_at,
        updated_at=new_position.updated_at,
        nomination_count=0
    )


@router.get("/positions", response_model=List[HiringPositionResponse])
def list_hiring_positions(
    status_filter: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all open hiring positions"""
    query = db.query(HiringPosition)
    
    # Filter by status if provided
    if status_filter:
        try:
            status_enum = HiringStatus(status_filter)
            query = query.filter(HiringPosition.status == status_enum)
        except ValueError:
            pass
    
    # Default to open positions if no filter
    if not status_filter:
        query = query.filter(HiringPosition.status == HiringStatus.OPEN)
    
    positions = query.order_by(HiringPosition.created_at.desc()).all()
    
    result = []
    for position in positions:
        nomination_count = db.query(func.count(AgentNomination.id)).filter(
            AgentNomination.hiring_position_id == position.id
        ).scalar()
        
        result.append(HiringPositionResponse(
            id=position.id,
            business_id=position.business_id,
            title=position.title,
            description=position.description,
            requirements=position.requirements,
            status=position.status,
            created_at=position.created_at,
            updated_at=position.updated_at,
            nomination_count=nomination_count
        ))
    
    return result


@router.get("/positions/{position_id}", response_model=HiringPositionWithNominations)
def get_hiring_position(
    position_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a hiring position with its nominations"""
    position = db.query(HiringPosition).filter(HiringPosition.id == position_id).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hiring position not found"
        )
    
    # Get nominations
    nominations = db.query(AgentNomination).filter(
        AgentNomination.hiring_position_id == position_id
    ).all()
    
    nomination_responses = []
    for nom in nominations:
        agent = db.query(Agent).filter(Agent.id == nom.agent_id).first()
        developer = db.query(User).filter(User.id == nom.developer_id).first()
        
        nomination_responses.append(AgentNominationResponse(
            id=nom.id,
            hiring_position_id=nom.hiring_position_id,
            agent_id=nom.agent_id,
            developer_id=nom.developer_id,
            cover_letter=nom.cover_letter,
            status=nom.status,
            reviewed_by=nom.reviewed_by,
            reviewed_at=nom.reviewed_at,
            review_notes=nom.review_notes,
            created_at=nom.created_at,
            agent_name=agent.name if agent else None,
            developer_email=developer.email if developer else None,
            hiring_position_title=position.title
        ))
    
    return HiringPositionWithNominations(
        id=position.id,
        business_id=position.business_id,
        title=position.title,
        description=position.description,
        requirements=position.requirements,
        status=position.status,
        created_at=position.created_at,
        updated_at=position.updated_at,
        nomination_count=len(nomination_responses),
        nominations=nomination_responses
    )


@router.put("/positions/{position_id}", response_model=HiringPositionResponse)
def update_hiring_position(
    position_id: int,
    position_data: HiringPositionUpdate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Update a hiring position (business owner only)"""
    position = db.query(HiringPosition).filter(HiringPosition.id == position_id).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hiring position not found"
        )
    
    if position.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this position"
        )
    
    update_data = position_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(position, field, value)
    
    from datetime import datetime
    position.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(position)
    
    nomination_count = db.query(func.count(AgentNomination.id)).filter(
        AgentNomination.hiring_position_id == position.id
    ).scalar()
    
    return HiringPositionResponse(
        id=position.id,
        business_id=position.business_id,
        title=position.title,
        description=position.description,
        requirements=position.requirements,
        status=position.status,
        created_at=position.created_at,
        updated_at=position.updated_at,
        nomination_count=nomination_count
    )


@router.delete("/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hiring_position(
    position_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Delete a hiring position (business owner only)"""
    position = db.query(HiringPosition).filter(HiringPosition.id == position_id).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hiring position not found"
        )
    
    if position.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this position"
        )
    
    db.delete(position)
    db.commit()
    return None


@router.post("/nominations", response_model=AgentNominationResponse, status_code=status.HTTP_201_CREATED)
def create_nomination(
    nomination_data: AgentNominationCreate,
    current_user: User = Depends(get_current_developer_user),
    db: Session = Depends(get_db)
):
    """Nominate an agent for a hiring position (developers only)"""
    # Verify hiring position exists and is open
    position = db.query(HiringPosition).filter(
        HiringPosition.id == nomination_data.hiring_position_id
    ).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hiring position not found"
        )
    
    if position.status != HiringStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This position is no longer accepting nominations"
        )
    
    # Verify agent belongs to the developer
    agent = db.query(Agent).filter(Agent.id == nomination_data.agent_id).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.developer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only nominate your own agents"
        )
    
    # Check if already nominated
    existing = db.query(AgentNomination).filter(
        AgentNomination.hiring_position_id == nomination_data.hiring_position_id,
        AgentNomination.agent_id == nomination_data.agent_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This agent has already been nominated for this position"
        )
    
    new_nomination = AgentNomination(
        hiring_position_id=nomination_data.hiring_position_id,
        agent_id=nomination_data.agent_id,
        developer_id=current_user.id,
        cover_letter=nomination_data.cover_letter
    )
    db.add(new_nomination)
    db.commit()
    db.refresh(new_nomination)
    
    return AgentNominationResponse(
        id=new_nomination.id,
        hiring_position_id=new_nomination.hiring_position_id,
        agent_id=new_nomination.agent_id,
        developer_id=new_nomination.developer_id,
        cover_letter=new_nomination.cover_letter,
        status=new_nomination.status,
        reviewed_by=new_nomination.reviewed_by,
        reviewed_at=new_nomination.reviewed_at,
        review_notes=new_nomination.review_notes,
        created_at=new_nomination.created_at,
        agent_name=agent.name,
        developer_email=current_user.email,
        hiring_position_title=position.title
    )


@router.get("/nominations", response_model=List[AgentNominationResponse])
def list_nominations(
    position_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List nominations (filtered by user role)"""
    query = db.query(AgentNomination)
    
    if current_user.role == UserRole.DEVELOPER:
        # Developers see only their own nominations
        query = query.filter(AgentNomination.developer_id == current_user.id)
    elif current_user.role == UserRole.BUSINESS:
        # Businesses see nominations for their positions
        positions = db.query(HiringPosition.id).filter(
            HiringPosition.business_id == current_user.id
        ).subquery()
        query = query.filter(AgentNomination.hiring_position_id.in_(db.query(positions.c.id)))
    
    if position_id:
        query = query.filter(AgentNomination.hiring_position_id == position_id)
    
    nominations = query.order_by(AgentNomination.created_at.desc()).all()
    
    result = []
    for nom in nominations:
        agent = db.query(Agent).filter(Agent.id == nom.agent_id).first()
        developer = db.query(User).filter(User.id == nom.developer_id).first()
        position = db.query(HiringPosition).filter(HiringPosition.id == nom.hiring_position_id).first()
        
        result.append(AgentNominationResponse(
            id=nom.id,
            hiring_position_id=nom.hiring_position_id,
            agent_id=nom.agent_id,
            developer_id=nom.developer_id,
            cover_letter=nom.cover_letter,
            status=nom.status,
            reviewed_by=nom.reviewed_by,
            reviewed_at=nom.reviewed_at,
            review_notes=nom.review_notes,
            created_at=nom.created_at,
            agent_name=agent.name if agent else None,
            developer_email=developer.email if developer else None,
            hiring_position_title=position.title if position else None
        ))
    
    return result


@router.put("/nominations/{nomination_id}/review", response_model=AgentNominationResponse)
def review_nomination(
    nomination_id: int,
    review_data: AgentNominationUpdate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Review a nomination (approve/reject) - business owner only"""
    nomination = db.query(AgentNomination).filter(AgentNomination.id == nomination_id).first()
    if not nomination:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nomination not found"
        )
    
    # Verify the position belongs to the current user
    position = db.query(HiringPosition).filter(
        HiringPosition.id == nomination.hiring_position_id
    ).first()
    
    if not position or position.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to review this nomination"
        )
    
    # Update nomination status
    if review_data.status:
        nomination.status = review_data.status
        from datetime import datetime
        nomination.reviewed_by = current_user.id
        nomination.reviewed_at = datetime.utcnow()
    
    if review_data.review_notes:
        nomination.review_notes = review_data.review_notes
    
    # If approved, activate the agent in marketplace
    if review_data.status == NominationStatus.APPROVED:
        agent = db.query(Agent).filter(Agent.id == nomination.agent_id).first()
        if agent:
            from models.agent import AgentStatus
            agent.status = AgentStatus.ACTIVE
    
    db.commit()
    db.refresh(nomination)
    
    agent = db.query(Agent).filter(Agent.id == nomination.agent_id).first()
    developer = db.query(User).filter(User.id == nomination.developer_id).first()
    
    return AgentNominationResponse(
        id=nomination.id,
        hiring_position_id=nomination.hiring_position_id,
        agent_id=nomination.agent_id,
        developer_id=nomination.developer_id,
        cover_letter=nomination.cover_letter,
        status=nomination.status,
        reviewed_by=nomination.reviewed_by,
        reviewed_at=nomination.reviewed_at,
        review_notes=nomination.review_notes,
        created_at=nomination.created_at,
        agent_name=agent.name if agent else None,
        developer_email=developer.email if developer else None,
        hiring_position_title=position.title
    )
