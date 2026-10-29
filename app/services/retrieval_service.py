from dataclasses import dataclass
from datetime import datetime
import math
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk, EMBEDDING_DIMENSIONS
from app.services.embedding_service import MODEL_NAME, generate_embeddings


@dataclass(frozen=True)
class SearchResult:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    cosine_distance: float
    similarity_score: float


def semantic_search(
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
) -> list[SearchResult]:
    """Return the most relevant embedded chunks for one tenant."""
    if not query or not query.strip():
        raise ValueError("Search query cannot be blank")

    if not 1 <= top_k <= settings.search_max_top_k:
        raise ValueError(
            f"top_k must be between 1 and {settings.search_max_top_k}"
        )
    if document_ids == []:
        return []

    query_embeddings = generate_embeddings([query.strip()])
    if len(query_embeddings) != 1:
        raise ValueError("Expected exactly one query embedding")

    query_embedding = query_embeddings[0]

    if len(query_embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Query embedding must contain {EMBEDDING_DIMENSIONS} values"
        )
    if not all(math.isfinite(value) for value in query_embedding):
        raise ValueError("Query embedding contains a non-finite value")

    distance = DocumentChunk.embedding.cosine_distance(query_embedding)

    statement = (
        select(DocumentChunk, distance.label("cosine_distance"))
        .join(
            Document,
            (Document.id == DocumentChunk.document_id)
            & (Document.tenant_id == DocumentChunk.tenant_id),
        )
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_model == MODEL_NAME,
            Document.status == DocumentStatus.COMPLETED,
        )
        .order_by(distance.asc(), DocumentChunk.id.asc())
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

    results: list[SearchResult] = []
    for chunk, cosine_distance in rows:
        distance_value = float(cosine_distance)
        results.append(
            SearchResult(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                cosine_distance=distance_value,
                similarity_score=1.0 - distance_value,
            )
        )

    return results
