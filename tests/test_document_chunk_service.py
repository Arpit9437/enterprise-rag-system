import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.tenants import Tenant
from app.services.document_chunk_service import save_document_chunks


@pytest.fixture
def tenant(db_session: Session) -> Tenant:
    tenant = Tenant(name=f"chunk-test-{uuid.uuid4().hex}")
    db_session.add(tenant)
    db_session.flush()
    return tenant


@pytest.fixture
def document(db_session: Session, tenant: Tenant) -> Document:
    document = Document(
        tenant_id=tenant.id,
        filename="test.txt",
    )
    db_session.add(document)
    db_session.flush()
    return document


def test_save_document_chunks_persists_all_chunks(
    db_session: Session,
    document: Document,
    tenant: Tenant,
) -> None:
    chunks = [
        "First chunk of text.",
        "Second chunk of text.",
        "Third chunk of text.",
    ]

    saved_chunks = save_document_chunks(
        db=db_session,
        document_id=document.id,
        tenant_id=tenant.id,
        chunks=chunks,
    )

    assert len(saved_chunks) == 3
    assert [chunk.chunk_index for chunk in saved_chunks] == [0, 1, 2]
    assert [chunk.content for chunk in saved_chunks] == chunks

    stored_chunks = db_session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()

    assert len(stored_chunks) == 3
    assert [chunk.content for chunk in stored_chunks] == chunks
    assert all(chunk.tenant_id == tenant.id for chunk in stored_chunks)


def test_save_document_chunks_returns_empty_list_for_empty_input(
    db_session: Session,
    document: Document,
    tenant: Tenant,
) -> None:
    saved_chunks = save_document_chunks(
        db=db_session,
        document_id=document.id,
        tenant_id=tenant.id,
        chunks=[],
    )

    assert saved_chunks == []

    stored_chunks = db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all()

    assert stored_chunks == []


def test_save_document_chunks_does_not_commit(
    db_session: Session,
    document: Document,
    tenant: Tenant,
) -> None:
    save_document_chunks(
        db=db_session,
        document_id=document.id,
        tenant_id=tenant.id,
        chunks=["Temporary chunk"],
    )

    # The service flushes but leaves the transaction under caller control.
    assert db_session.in_transaction()

    db_session.rollback()

    stored_chunks = db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all()

    assert stored_chunks == []


def test_save_document_chunks_rejects_nonexistent_document(
    db_session: Session,
    tenant: Tenant,
) -> None:
    nonexistent_document_id = uuid.uuid4()

    with pytest.raises(IntegrityError):
        save_document_chunks(
            db=db_session,
            document_id=nonexistent_document_id,
            tenant_id=tenant.id,
            chunks=["This should violate the foreign key"],
        )

    # A failed flush leaves the session transaction in a failed state.
    db_session.rollback()
