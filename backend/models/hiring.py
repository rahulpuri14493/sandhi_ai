from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from db.database import Base


class HiringStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    FILLED = "filled"


class NominationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class HiringPosition(Base):
    __tablename__ = "hiring_positions"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    requirements = Column(Text)  # Roles and responsibilities
    status = Column(Enum(HiringStatus), default=HiringStatus.OPEN)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    business = relationship("User", foreign_keys=[business_id])
    nominations = relationship("AgentNomination", back_populates="hiring_position", cascade="all, delete-orphan")


class AgentNomination(Base):
    __tablename__ = "agent_nominations"

    id = Column(Integer, primary_key=True, index=True)
    hiring_position_id = Column(Integer, ForeignKey("hiring_positions.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    developer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    cover_letter = Column(Text)  # Optional message from developer
    status = Column(Enum(NominationStatus), default=NominationStatus.PENDING)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # Business user who reviewed
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)  # Notes from reviewer
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    hiring_position = relationship("HiringPosition", back_populates="nominations")
    agent = relationship("Agent", foreign_keys=[agent_id])
    developer = relationship("User", foreign_keys=[developer_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
