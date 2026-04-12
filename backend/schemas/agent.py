from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from models.agent import AgentStatus, PricingModel


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    capabilities: Optional[List[str]] = Field(default_factory=list)
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    pricing_model: PricingModel = PricingModel.PAY_PER_USE
    price_per_task: float = Field(default=0.0, ge=0, le=1_000_000)
    price_per_communication: float = Field(default=0.0, ge=0, le=1_000_000)
    monthly_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    quarterly_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = Field(default=None, max_length=2048)  # API key for authenticated endpoints
    llm_model: Optional[str] = Field(default=None, max_length=120)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    plugin_config: Optional[Dict[str, Any]] = None
    a2a_enabled: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        normalized = [c.strip() for c in value if isinstance(c, str) and c.strip()]
        # Keep order but deduplicate exact matches.
        seen = set()
        out = []
        for item in normalized:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    @model_validator(mode="after")
    def validate_pricing_consistency(self):
        if self.pricing_model == PricingModel.MONTHLY and self.monthly_price is None:
            raise ValueError("monthly_price is required when pricing_model is monthly")
        if self.pricing_model == PricingModel.QUARTERLY and self.quarterly_price is None:
            raise ValueError("quarterly_price is required when pricing_model is quarterly")
        return self


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    capabilities: Optional[List[str]] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    pricing_model: Optional[PricingModel] = None
    price_per_task: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    price_per_communication: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    monthly_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    quarterly_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = Field(default=None, max_length=2048)
    llm_model: Optional[str] = Field(default=None, max_length=120)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
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