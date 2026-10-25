from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import EmailStr

from pydantic import BaseModel, ConfigDict, Field

TenantRole = Literal["admin", "member"]


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime

class TenantMemberAdd(BaseModel):
    email: EmailStr
    role: TenantRole = "member"


class TenantMemberRoleUpdate(BaseModel):
    role: TenantRole


class TenantMemberResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    role: TenantRole


class TenantMemberListResponse(BaseModel):
    members: list[TenantMemberResponse]