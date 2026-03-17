from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from db.database import get_db
from models.transaction import Transaction
from schemas.transaction import TransactionResponse
from services.payment_processor import PaymentProcessor
from core.security import get_current_user
from models.user import User

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/calculate")
def calculate_cost(job_id: int, db: Session = Depends(get_db)):
    """Calculate total cost for a job"""
    payment_processor = PaymentProcessor(db)
    preview = payment_processor.calculate_job_cost(job_id)
    return preview


@router.post("/process", response_model=TransactionResponse)
def process_payment(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Process payment for a job (mock implementation)"""
    payment_processor = PaymentProcessor(db)
    transaction = payment_processor.process_payment(job_id)
    return transaction


@router.get("/transactions", response_model=List[TransactionResponse])
def list_transactions(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """List transactions for the current user"""
    transactions = (
        db.query(Transaction).filter(Transaction.payer_id == current_user.id).all()
    )
    return transactions
