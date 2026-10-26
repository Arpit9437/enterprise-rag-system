from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.services.document_chunk_service import save_document_chunks
from app.services.document_processing import chunk_text, extract_text


def process_document(
    db: Session,
    document_id: UUID,
    tenant_id: UUID,
) -> int:
    """Extract, chunk, and persist a document's text.

    The caller owns the transaction and must commit or roll back.
    Returns the number of chunks created.
    """
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )

    if document is None:
        raise ValueError("Document not found for the specified tenant")

    if not document.storage_key or not document.content_type:
        raise ValueError("Document has no stored file metadata")

    # Remove previously generated chunks so reprocessing is idempotent.
    db.query(DocumentChunk).filter(
        DocumentChunk.document_id == document.id,
        DocumentChunk.tenant_id == tenant_id,
    ).delete(synchronize_session=False)

    document.status = DocumentStatus.PROCESSING
    db.flush()

    try:
        text = extract_text(
            storage_key=document.storage_key,
            content_type=document.content_type,
        )
        chunks = chunk_text(text)

        if not chunks:
            raise ValueError("Document produced no text chunks")

        save_document_chunks(
            db=db,
            document_id=document.id,
            tenant_id=tenant_id,
            chunks=chunks,
        )

        document.status = DocumentStatus.COMPLETED
        db.flush()

        return len(chunks)

    except Exception:
        # The caller must roll back to undo partial DB changes.
        raise
