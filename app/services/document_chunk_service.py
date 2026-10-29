import math
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.document_chunk import DocumentChunk, EMBEDDING_DIMENSIONS


def save_document_chunks(
    db: Session,
    document_id: UUID,
    tenant_id: UUID,
    chunks: list[str],
    embeddings: list[list[float]] | None = None,
    embedding_model: str | None = None,
) -> list[DocumentChunk]:
    """Add chunks and optional embeddings without committing."""
    if embeddings is not None and len(embeddings) != len(chunks):
        raise ValueError(
            "The number of embeddings must match the number of chunks"
        )

    if not chunks:
        if embeddings:
            raise ValueError("Embeddings cannot be supplied without chunks")
        return []

    document_chunks: list[DocumentChunk] = []

    for index, content in enumerate(chunks):
        embedding = embeddings[index] if embeddings is not None else None

        if embedding is not None and len(embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Each embedding must contain {EMBEDDING_DIMENSIONS} values"
            )
        if embedding is not None and not all(
            math.isfinite(value) for value in embedding
        ):
            raise ValueError("Each embedding must contain finite values")

        document_chunks.append(
            DocumentChunk(
                document_id=document_id,
                tenant_id=tenant_id,
                chunk_index=index,
                content=content,
                embedding=embedding,
                embedding_model=embedding_model,
            )
        )

    db.add_all(document_chunks)
    db.flush()

    return document_chunks
