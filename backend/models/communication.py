from sqlalchemy import Column, Integer, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from db.database import Base


class AgentCommunication(Base):
    """
    Represents a communication between two workflow steps or agents.
    """

    __tablename__ = "agent_communications"

    id = Column(Integer, primary_key=True, index=True)
    from_workflow_step_id = Column(
        Integer, ForeignKey("workflow_steps.id"), nullable=False
    )
    to_workflow_step_id = Column(
        Integer, ForeignKey("workflow_steps.id"), nullable=False
    )
    from_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    to_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    data_transferred = Column(Text)  # JSON string
    cost = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationships
    from_step = relationship(
        "WorkflowStep",
        foreign_keys=[from_workflow_step_id],
        back_populates="communications_from",
        lazy="joined",
    )
    to_step = relationship(
        "WorkflowStep",
        foreign_keys=[to_workflow_step_id],
        back_populates="communications_to",
        lazy="joined",
    )
    from_agent = relationship(
        "Agent",
        foreign_keys=[from_agent_id],
        back_populates="communications_from",
        lazy="joined",
    )
    to_agent = relationship(
        "Agent",
        foreign_keys=[to_agent_id],
        back_populates="communications_to",
        lazy="joined",
    )
