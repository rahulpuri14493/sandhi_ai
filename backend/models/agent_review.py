"""Agent review and rating model. Users can submit multiple reviews per agent."""
from sqlalchemy import Column, Integer, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from db.database import Base


class AgentReview(Base):
    """User-submitted rating and optional review text for an agent. Multiple reviews per user allowed."""

    __tablename__ = "agent_reviews"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating = Column(Float, nullable=False)  # 1.0 to 5.0
    review_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    agent = relationship("Agent", backref="reviews")
    user = relationship("User", backref="agent_reviews")
