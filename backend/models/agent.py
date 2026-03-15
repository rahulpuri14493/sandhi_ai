from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, Enum, Boolean
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from db.database import Base


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


class PricingModel(str, enum.Enum):
    PAY_PER_USE = "pay_per_use"  # Price per task/communication
    MONTHLY = "monthly"  # Monthly subscription
    QUARTERLY = "quarterly"  # Quarterly subscription


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    developer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    capabilities = Column(JSON)  # List of capability strings
    input_schema = Column(JSON)  # JSON schema for inputs
    output_schema = Column(JSON)  # JSON schema for outputs
    pricing_model = Column(
        Enum(PricingModel, name='pricingmodel', create_type=False, native_enum=True, values_callable=lambda x: [e.value for e in x]),
        default=PricingModel.PAY_PER_USE,
    )
    price_per_task = Column(Float, nullable=False, default=0.0)
    price_per_communication = Column(Float, nullable=False, default=0.0)
    monthly_price = Column(Float, nullable=True, default=None)  # Monthly subscription price
    quarterly_price = Column(Float, nullable=True, default=None)  # Quarterly subscription price
    api_endpoint = Column(String)  # For API-based agents
    api_key = Column(String)  # API key for authenticated endpoints (stored encrypted in production)
    llm_model = Column(String, nullable=True, default=None)  # Model name for OpenAI-compatible endpoints
    temperature = Column(Float, nullable=True, default=None)  # Sampling temperature for OpenAI-compatible endpoints
    plugin_config = Column(JSON)  # For plugin-based agents
    a2a_enabled = Column(Boolean, default=False, nullable=False)  # Use A2A protocol for invocation when True
    status = Column(Enum(AgentStatus), default=AgentStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    developer = relationship("User", back_populates="agents", foreign_keys=[developer_id])
    workflow_steps = relationship("WorkflowStep", back_populates="agent")
    communications_from = relationship("AgentCommunication", foreign_keys="AgentCommunication.from_agent_id", back_populates="from_agent")
    communications_to = relationship("AgentCommunication", foreign_keys="AgentCommunication.to_agent_id", back_populates="to_agent")
