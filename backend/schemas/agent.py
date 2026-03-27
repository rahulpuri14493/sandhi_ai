from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from models.agent import AgentStatus, PricingModel


class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    capabilities: Optional[List[str]] = []
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    pricing_model: PricingModel = PricingModel.PAY_PER_USE
    price_per_task: float = 0.0
    price_per_communication: float = 0.0
    monthly_price: Optional[float] = None
    quarterly_price: Optional[float] = None
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None  # API key for authenticated endpoints
    llm_model: Optional[str] = None
    temperature: Optional[float] = None
    plugin_config: Optional[Dict[str, Any]] = None
    a2a_enabled: bool = False


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[List[str]] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    pricing_model: Optional[PricingModel] = None
    price_per_task: Optional[float] = None
    price_per_communication: Optional[float] = None
    monthly_price: Optional[float] = None
    quarterly_price: Optional[float] = None
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None
    llm_model: Optional[str] = None
    temperature: Optional[float] = None
    plugin_config: Optional[Dict[str, Any]] = None
    status: Optional[AgentStatus] = None
    a2a_enabled: Optional[bool] = None


class AgentResponse(BaseModel):
    id: int
    developer_id: int
    name: str
    description: Optional[str]
    capabilities: Optional[List[str]]
    input_schema: Optional[Dict[str, Any]]
    output_schema: Optional[Dict[str, Any]]
    pricing_model: PricingModel
    price_per_task: float
    price_per_communication: float
    monthly_price: Optional[float] = None
    quarterly_price: Optional[float] = None
    api_endpoint: Optional[str]
    api_key: Optional[str] = None  # Never return API key in responses for security
    llm_model: Optional[str] = None
    temperature: Optional[float] = None
    plugin_config: Optional[Dict[str, Any]]
    a2a_enabled: bool = False
    status: AgentStatus
    created_at: datetime
    # Optional: included in list for marketplace cards
    average_rating: Optional[float] = None
    review_count: Optional[int] = None

    class Config:
        from_attributes = True
    
    @classmethod
    def model_validate(cls, obj, **kwargs):
        # Exclude api_key from response for security
        if hasattr(obj, '__dict__'):
            obj_dict = obj.__dict__.copy()
            obj_dict.pop('api_key', None)
            obj = type(obj)(**{k: v for k, v in obj_dict.items() if k in obj.__table__.columns.keys()})
        return super().model_validate(obj, **kwargs)


"""
Note: If we later reintroduce a public marketplace endpoint, use a dedicated response
model with non-enumerable IDs. For now, APIsec findings are addressed by enforcing
authentication on the flagged endpoints.
"""