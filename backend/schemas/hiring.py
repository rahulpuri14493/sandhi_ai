from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from models.hiring import HiringStatus, NominationStatus


class HiringPositionCreate(BaseModel):
    title: str
    description: Optional[str] = None
    requirements: Optional[str] = None


class HiringPositionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    requirements: Optional[str] = None
    status: Optional[HiringStatus] = None


class HiringPositionResponse(BaseModel):
    id: int
    business_id: int
    title: str
    description: Optional[str]
    requirements: Optional[str]
    status: HiringStatus
    created_at: datetime
    updated_at: datetime
    nomination_count: Optional[int] = 0

    class Config:
        from_attributes = True


class AgentNominationCreate(BaseModel):
    hiring_position_id: int
    agent_id: int
    cover_letter: Optional[str] = None


class AgentNominationUpdate(BaseModel):
    status: Optional[NominationStatus] = None
    review_notes: Optional[str] = None


class AgentNominationResponse(BaseModel):
    id: int
    hiring_position_id: int
    agent_id: int
    developer_id: int
    cover_letter: Optional[str]
    status: NominationStatus
    reviewed_by: Optional[int]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    created_at: datetime
    agent_name: Optional[str] = None
    developer_email: Optional[str] = None
    hiring_position_title: Optional[str] = None

    class Config:
        from_attributes = True


class HiringPositionWithNominations(HiringPositionResponse):
    nominations: List[AgentNominationResponse] = []
