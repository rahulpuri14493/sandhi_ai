from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from db.database import Base


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, unique=True)
    payer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    total_amount = Column(Float, nullable=False)
    platform_commission = Column(Float, nullable=False)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    job = relationship("Job", back_populates="transaction")
    payer = relationship("User", back_populates="transactions")
    earnings = relationship("Earnings", back_populates="transaction")


class EarningsStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class Earnings(Base):
    __tablename__ = "earnings"

    id = Column(Integer, primary_key=True, index=True)
    developer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    workflow_step_id = Column(Integer, ForeignKey("workflow_steps.id"), nullable=True)
    communication_id = Column(Integer, ForeignKey("agent_communications.id"), nullable=True)
    amount = Column(Float, nullable=False)
    status = Column(Enum(EarningsStatus), default=EarningsStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    developer = relationship("User", back_populates="earnings")
    transaction = relationship("Transaction", back_populates="earnings")
