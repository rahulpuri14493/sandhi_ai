from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from db.database import Base


class QAStatus(str, enum.Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    CLARIFIED = "clarified"


class JobQuestion(Base):
    __tablename__ = "job_questions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    status = Column(Enum(QAStatus), default=QAStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    answered_at = Column(DateTime, nullable=True)

    # Relationships
    job = relationship("Job", back_populates="questions")
