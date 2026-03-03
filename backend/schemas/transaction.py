from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from models.transaction import TransactionStatus, EarningsStatus


class TransactionResponse(BaseModel):
    id: int
    job_id: int
    payer_id: int
    total_amount: float
    platform_commission: float
    status: TransactionStatus
    created_at: datetime

    class Config:
        from_attributes = True


class EarningsResponse(BaseModel):
    id: int
    developer_id: int
    transaction_id: int
    workflow_step_id: Optional[int]
    communication_id: Optional[int]
    amount: float
    status: EarningsStatus
    created_at: datetime

    class Config:
        from_attributes = True
