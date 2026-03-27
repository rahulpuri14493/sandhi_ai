from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List
from datetime import datetime
from models.hiring import HiringStatus, NominationStatus


class HiringPositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    requirements: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class HiringPositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=2, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    requirements: Optional[str] = Field(default=None, max_length=4000)
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
    model_config = ConfigDict(extra="forbid")

    hiring_position_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    cover_letter: Optional[str] = Field(default=None, max_length=3000)


class AgentNominationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[NominationStatus] = None
    review_notes: Optional[str] = Field(default=None, max_length=3000)


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
