from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AgentCommunicationResponse(BaseModel):
    id: int
    from_workflow_step_id: int
    to_workflow_step_id: int
    from_agent_id: int
    to_agent_id: int
    data_transferred: Optional[str]
    cost: float
    timestamp: datetime

    class Config:
        from_attributes = True
