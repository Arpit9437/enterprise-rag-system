from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.config import settings


class RetrievalFilters(BaseModel):
    document_ids: list[UUID] | None = Field(
        default=None,
        max_length=20,
        description="Optional document IDs to include, scoped to the path tenant.",
    )
    content_type: Literal["application/pdf", "text/plain"] | None = Field(
        default=None,
        description="Optional uploaded document media type filter.",
    )
    created_after: datetime | None = Field(
        default=None,
        description="Inclusive UTC-aware creation timestamp lower bound.",
    )
    created_before: datetime | None = Field(
        default=None,
        description="Inclusive UTC-aware creation timestamp upper bound.",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "RetrievalFilters":
        for field_name in ("created_after", "created_before"):
            date_value = getattr(self, field_name)
            if date_value is not None and date_value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after > self.created_before
        ):
            raise ValueError("created_after must not be after created_before")
        return self


class SearchRequest(RetrievalFilters):
    query: str = Field(
        min_length=1,
        max_length=2000,
        description="Non-blank query text, at most 2,000 characters.",
        examples=["parental leave eligibility"],
    )
    top_k: int = Field(
        default=min(
            settings.search_default_top_k,
            settings.search_max_top_k,
        ),
        ge=1,
        le=settings.search_max_top_k,
        description=(
            "Maximum number of hybrid-ranked passages to return; bounded by "
            f"the server setting ({settings.search_max_top_k})."
        ),
    )

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Search query cannot be blank")
        return value.strip()


class SearchResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    semantic_score: float | None
    keyword_score: float | None
    fused_score: float


class SearchResponse(BaseModel):
    query: str = Field(description="Normalized query supplied by the caller.")
    results: list[SearchResultResponse] = Field(
        description=(
            "Hybrid-ranked passages from completed documents in the "
            "authorized tenant."
        )
    )
    total_results: int = Field(
        description="Number of results returned on this response."
    )