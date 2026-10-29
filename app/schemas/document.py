from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus


class DocumentCreate(BaseModel):
    filename: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Display filename only. Metadata creation does not upload a file "
            "or start processing."
        ),
    )


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    filename: str
    status: DocumentStatus = Field(
        description=(
            "Lifecycle state: pending, processing, completed, or failed. "
            "Only completed documents are searchable."
        )
    )
    created_at: datetime
    content_type: str | None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse] = Field(
        description="Page of documents, ordered newest first with stable ID tie-break."
    )
    total: int = Field(description="Total tenant documents across all pages.")
    limit: int = Field(description="Maximum number of documents in this page.")
    offset: int = Field(description="Number of matching documents skipped.")
