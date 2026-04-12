from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from datetime import datetime
from models.user import UserRole


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        # Baseline production-safe policy without over-constraining symbols/locales.
        if value.strip() != value:
            raise ValueError("Password must not start or end with whitespace")
        if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
            raise ValueError("Password must include both letters and numbers")
        return value


class UserLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: int
    email: str
    role: UserRole
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str
