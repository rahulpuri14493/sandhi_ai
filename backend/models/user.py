from sqlalchemy import Column, Integer, String, DateTime, Enum
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from db.database import Base


class UserRole(str, enum.Enum):
    BUSINESS = "business"
    DEVELOPER = "developer"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    agents = relationship(
        "Agent", back_populates="developer", foreign_keys="Agent.developer_id"
    )
    jobs = relationship(
        "Job", back_populates="business", foreign_keys="Job.business_id"
    )
    transactions = relationship("Transaction", back_populates="payer")
    earnings = relationship("Earnings", back_populates="developer")
