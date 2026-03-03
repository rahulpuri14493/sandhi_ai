from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from models.job_qa import QAStatus


class JobQuestionResponse(BaseModel):
    id: int
    job_id: int
    question: str
    answer: Optional[str]
    status: QAStatus
    created_at: datetime
    answered_at: Optional[datetime]

    class Config:
        from_attributes = True


class AnswerQuestionRequest(BaseModel):
    answer: str
