from uuid import UUID

from sqlalchemy.orm import Session

from app.models.document_chunk import DocumentChunk


def save_document_chunks(
    db: Session,
    document_id: UUID,
    tenant_id: UUID,
    chunks: list[str],
) -> list[DocumentChunk]:
    """Persist document chunks within the caller's transaction.

    Chunk indices start at zero. This function flushes changes to the
    database but does not commit the transaction.
    """
    if not chunks:
        return []

    document_chunks = [
        DocumentChunk(
            document_id=document_id,
            tenant_id=tenant_id,
            chunk_index=index,
            content=content,
        )
        for index, content in enumerate(chunks)
    ]

    db.add_all(document_chunks)
    db.flush()

    return document_chunks
