import math
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.observability import observe
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk, EMBEDDING_DIMENSIONS
from app.services.document_chunk_service import save_document_chunks
from app.services.document_processing import chunk_text, extract_text
from app.services.embedding_service import MODEL_NAME, generate_embeddings


@dataclass(frozen=True)
class PreparedDocument:
    chunks: list[str]
    embeddings: list[list[float]]
    chunk_size: int
    chunk_overlap: int
    embedding_model: str


class LostProcessingClaim(RuntimeError):
    pass


def prepare_document(
    storage_key: str,
    content_type: str,
) -> PreparedDocument:
    started_at = time.perf_counter()
    try:
        text = extract_text(
            storage_key=storage_key,
            content_type=content_type,
        )
    except Exception:
        observe(
            "document_stage_duration_seconds",
            time.perf_counter() - started_at,
            stage="extraction",
            outcome="failure",
        )
        raise
    observe(
        "document_stage_duration_seconds",
        time.perf_counter() - started_at,
        stage="extraction",
        outcome="success",
    )

    started_at = time.perf_counter()
    try:
        chunks = chunk_text(text)
    except Exception:
        observe(
            "document_stage_duration_seconds",
            time.perf_counter() - started_at,
            stage="chunking",
            outcome="failure",
        )
        raise
    if not chunks:
        observe(
            "document_stage_duration_seconds",
            time.perf_counter() - started_at,
            stage="chunking",
            outcome="failure",
        )
        raise ValueError("Document produced no text chunks")
    observe(
        "document_stage_duration_seconds",
        time.perf_counter() - started_at,
        stage="chunking",
        outcome="success",
    )

    started_at = time.perf_counter()
    try:
        embeddings = generate_embeddings(chunks)
    except Exception:
        observe(
            "document_stage_duration_seconds",
            time.perf_counter() - started_at,
            stage="embedding",
            outcome="failure",
        )
        raise
    observe(
        "document_stage_duration_seconds",
        time.perf_counter() - started_at,
        stage="embedding",
        outcome="success",
    )
    if len(embeddings) != len(chunks):
        raise ValueError("Embedding count does not match chunk count")

    for embedding in embeddings:
        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Each embedding must contain {EMBEDDING_DIMENSIONS} values"
            )
        if not all(math.isfinite(value) for value in embedding):
            raise ValueError("Embedding contains a non-finite value")

    return PreparedDocument(
        chunks=chunks,
        embeddings=embeddings,
        chunk_size=settings.document_chunk_size,
        chunk_overlap=settings.document_chunk_overlap,
        embedding_model=MODEL_NAME,
    )


def persist_prepared_document(
    db: Session,
    document_id: UUID,
    tenant_id: UUID,
    prepared: PreparedDocument,
    claim_id: UUID | None = None,
) -> int:
    with db.begin_nested():
        completion = update(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
        if claim_id is not None:
            completion = completion.where(
                Document.status == DocumentStatus.PROCESSING,
                Document.processing_claim_id == claim_id,
            )

        result = db.execute(
            completion.values(
                status=DocumentStatus.COMPLETED,
                processing_claimed_at=None,
                processing_claim_id=None,
                chunk_size_used=prepared.chunk_size,
                chunk_overlap_used=prepared.chunk_overlap,
            )
        )
        if result.rowcount != 1:
            raise LostProcessingClaim(
                f"Document {document_id} processing claim is no longer active"
            )

        db.query(DocumentChunk).filter(
            DocumentChunk.document_id == document_id,
            DocumentChunk.tenant_id == tenant_id,
        ).delete(synchronize_session=False)

        chunks = save_document_chunks(
            db=db,
            document_id=document_id,
            tenant_id=tenant_id,
            chunks=prepared.chunks,
            embeddings=prepared.embeddings,
            embedding_model=prepared.embedding_model,
        )

        db.flush()

    return len(prepared.chunks)


def process_document(
    db: Session,
    document_id: UUID,
    tenant_id: UUID,
) -> int:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )

    if document is None:
        raise ValueError("Document not found")

    if not document.storage_key or not document.content_type:
        raise ValueError("Document has no stored file metadata")

    prepared = prepare_document(document.storage_key, document.content_type)
    return persist_prepared_document(
        db=db,
        document_id=document_id,
        tenant_id=tenant_id,
        prepared=prepared,
    )
