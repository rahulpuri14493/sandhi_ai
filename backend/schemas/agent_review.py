"""Pydantic schemas for agent reviews and ratings."""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime

# Rating must be 1-5 (industry standard 5-star)
RATING_MIN = 1
RATING_MAX = 5
REVIEW_TEXT_MAX_LENGTH = 2000


def _empty_str_to_none(v: Optional[str]) -> Optional[str]:
    """Treat empty or whitespace-only string as None (review text is optional)."""
    if v is None:
        return None
    s = v.strip() if isinstance(v, str) else v
    return s if s else None


class AgentReviewCreate(BaseModel):
    """Payload to create or update a user's review for an agent. Review text is optional."""

    rating: float = Field(
        ..., ge=RATING_MIN, le=RATING_MAX, description="Rating from 1 to 5"
    )
    review_text: Optional[str] = Field(None, max_length=REVIEW_TEXT_MAX_LENGTH)

    @field_validator("review_text", mode="before")
    @classmethod
    def review_text_optional(cls, v: Optional[str]) -> Optional[str]:
        return _empty_str_to_none(v)


class AgentReviewUpdate(BaseModel):
    """Payload to update only provided fields of a review."""

    rating: Optional[float] = Field(None, ge=RATING_MIN, le=RATING_MAX)
    review_text: Optional[str] = Field(None, max_length=REVIEW_TEXT_MAX_LENGTH)

    @field_validator("review_text", mode="before")
    @classmethod
    def review_text_optional(cls, v: Optional[str]) -> Optional[str]:
        return _empty_str_to_none(v)


class AgentReviewResponse(BaseModel):
    """Single review as returned by the API. Does not expose full user identity for privacy."""

    id: int
    agent_id: int
    user_id: int
    rating: float
    review_text: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime] = None
    is_own: bool = False  # True when the requester is the review author

    class Config:
        from_attributes = True


class AgentReviewSummaryResponse(BaseModel):
    """Aggregate summary for an agent's reviews (public)."""

    agent_id: int
    average_rating: float
    total_count: int
    rating_distribution: dict[int, int]  # e.g. {1: 0, 2: 1, 3: 2, 4: 5, 5: 10}


class AgentReviewListResponse(BaseModel):
    """Paginated list of reviews."""

    items: list[AgentReviewResponse]
    total: int
    limit: int
    offset: int
