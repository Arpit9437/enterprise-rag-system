
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus


class DocumentCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    filename: str
    status: DocumentStatus
    created_at: datetime
    storage_key: str | None
    content_type: str | None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
