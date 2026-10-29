from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk


@dataclass(frozen=True)
class KeywordSearchResult:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    keyword_score: float


def keyword_search(
    db: Session,
    tenant_id: UUID,
    query: str,
    top_k: int = min(
        settings.search_default_top_k,
        settings.search_max_top_k,
    ),
    document_ids: list[UUID] | None = None,
    content_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[KeywordSearchResult]:
    """Search completed document chunks using PostgreSQL full-text search."""

    if not query or not query.strip():
        raise ValueError("Search query cannot be blank")

    if not 1 <= top_k <= settings.search_max_top_k:
        raise ValueError(
            f"top_k must be between 1 and {settings.search_max_top_k}"
        )
    if document_ids == []:
        return []

    cleaned_query = query.strip()

    # Convert chunk content into PostgreSQL's searchable text representation.
    searchable_content = func.to_tsvector(
        "english",
        DocumentChunk.content,
    )

    # Interpret the user's input as a plain-text search query.
    search_query = func.plainto_tsquery(
        "english",
        cleaned_query,
    )

    # Calculate a relevance score for each matching chunk.
    score = cast(
        func.ts_rank(searchable_content, search_query),
        Float,
    )

    statement = (
        select(DocumentChunk, score.label("keyword_score"))
        .join(
            Document,
            (Document.id == DocumentChunk.document_id)
            & (Document.tenant_id == DocumentChunk.tenant_id),
        )
        .where(
            DocumentChunk.tenant_id == tenant_id,
            Document.status == DocumentStatus.COMPLETED,
            searchable_content.bool_op("@@")(search_query),
        )
        .order_by(
            score.desc(),
            DocumentChunk.chunk_index.asc(),
            DocumentChunk.id.asc(),
        )
        .limit(top_k)
    )

    if document_ids is not None:
        statement = statement.where(Document.id.in_(document_ids))
    if content_type is not None:
        statement = statement.where(Document.content_type == content_type)
    if created_after is not None:
        statement = statement.where(Document.created_at >= created_after)
    if created_before is not None:
        statement = statement.where(Document.created_at <= created_before)

    rows = db.execute(statement).all()

    return [
        KeywordSearchResult(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            keyword_score=float(keyword_score),
        )
        for chunk, keyword_score in rows
    ]
