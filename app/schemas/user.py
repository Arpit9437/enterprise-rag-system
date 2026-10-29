from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

class UserRegister(BaseModel):
    email: EmailStr = Field(
        max_length=100,
        description="Unique email identity, normalized to lowercase.",
        examples=["person@example.com"],
    )
    password: str = Field(
        min_length=8,
        max_length=1024,
        description="Password of at least 8 characters; only its hash is stored.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    is_active: bool
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserLogin(BaseModel):
    email: EmailStr = Field(
        max_length=100,
        description="Account email address.",
    )
    password: str = Field(
        min_length=1,
        max_length=1024,
        description="Account password; invalid credentials return 401.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value