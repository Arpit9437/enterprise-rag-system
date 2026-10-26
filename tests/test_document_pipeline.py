import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.tenants import Tenant
from app.services.document_pipeline import process_document


@pytest.fixture
def tenant(db_session: Session) -> Tenant:
    tenant = Tenant(name=f"pipeline-test-{uuid.uuid4().hex}")
    db_session.add(tenant)
    db_session.flush()
    return tenant


@pytest.fixture
def document(db_session: Session, tenant: Tenant) -> Document:
    document = Document(
        tenant_id=tenant.id,
        filename="test.txt",
        storage_key=f"{uuid.uuid4()}.txt",
        content_type="text/plain",
        status=DocumentStatus.PENDING,
    )
    db_session.add(document)
    db_session.flush()
    return document


def test_process_document_saves_chunks_and_completes(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    extracted_text = "First paragraph. Second paragraph."
    expected_chunks = ["First paragraph.", "Second paragraph."]

    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value=extracted_text,
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=expected_chunks,
        ),
    ):
        count = process_document(
            db=db_session,
            document_id=document.id,
            tenant_id=tenant.id,
        )

    assert count == 2
    assert document.status == DocumentStatus.COMPLETED

    saved_chunks = db_session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()

    assert [chunk.content for chunk in saved_chunks] == expected_chunks
    assert [chunk.chunk_index for chunk in saved_chunks] == [0, 1]
    assert all(chunk.tenant_id == tenant.id for chunk in saved_chunks)


def test_process_document_rejects_unknown_document(
    db_session: Session,
    tenant: Tenant,
) -> None:
    with pytest.raises(ValueError, match="Document not found"):
        process_document(
            db=db_session,
            document_id=uuid.uuid4(),
            tenant_id=tenant.id,
        )


def test_process_document_rejects_wrong_tenant(
    db_session: Session,
    document: Document,
) -> None:
    with pytest.raises(ValueError, match="Document not found"):
        process_document(
            db=db_session,
            document_id=document.id,
            tenant_id=uuid.uuid4(),
        )


def test_process_document_rejects_missing_storage_metadata(
    db_session: Session,
    tenant: Tenant,
) -> None:
    document = Document(
        tenant_id=tenant.id,
        filename="missing.txt",
        status=DocumentStatus.PENDING,
    )
    db_session.add(document)
    db_session.flush()

    with pytest.raises(ValueError, match="no stored file metadata"):
        process_document(
            db=db_session,
            document_id=document.id,
            tenant_id=tenant.id,
        )


def test_process_document_rejects_empty_chunks(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Some extracted text",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=[],
        ),
    ):
        with pytest.raises(ValueError, match="no text chunks"):
            process_document(
                db=db_session,
                document_id=document.id,
                tenant_id=tenant.id,
            )

    # The service doesn't commit. The caller rolls back on failure.
    db_session.rollback()


def test_process_document_can_be_reprocessed(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Updated document content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Updated chunk"],
        ),
    ):
        first_count = process_document(
            db=db_session,
            document_id=document.id,
            tenant_id=tenant.id,
        )

        second_count = process_document(
            db=db_session,
            document_id=document.id,
            tenant_id=tenant.id,
        )

    assert first_count == 1
    assert second_count == 1

    saved_chunks = db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all()

    assert len(saved_chunks) == 1
    assert saved_chunks[0].content == "Updated chunk"
