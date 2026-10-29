import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.tenants import Tenant
from app.services.document_pipeline import prepare_document, process_document


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
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384, [0.0] * 384],
        )
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
    assert all(
        chunk.embedding_model
        == "sentence-transformers/all-MiniLM-L6-v2"
        for chunk in saved_chunks
    )
    assert document.chunk_size_used == 1000
    assert document.chunk_overlap_used == 150


def test_prepare_document_records_successful_stage_durations():
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Extracted text",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Extracted text"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384],
        ),
        patch("app.services.document_pipeline.observe") as observe,
    ):
        prepared = prepare_document("stored.txt", "text/plain")

    assert prepared.chunks == ["Extracted text"]
    assert [
        call.kwargs
        for call in observe.call_args_list
    ] == [
        {"stage": "extraction", "outcome": "success"},
        {"stage": "chunking", "outcome": "success"},
        {"stage": "embedding", "outcome": "success"},
    ]
    assert all(call.args[1] >= 0 for call in observe.call_args_list)


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
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384],
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
    assert saved_chunks[0].embedding is not None
    assert len(saved_chunks[0].embedding) == 384


def test_failed_reprocessing_preserves_existing_chunks(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Original content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Original chunk"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384],
        ),
    ):
        process_document(
            db=db_session,
            document_id=document.id,
            tenant_id=tenant.id,
        )

    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Updated content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Updated chunk"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            side_effect=RuntimeError("Embedding service failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="Embedding service failed"):
            process_document(
                db=db_session,
                document_id=document.id,
                tenant_id=tenant.id,
            )

    saved_chunks = db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all()

    assert [chunk.content for chunk in saved_chunks] == ["Original chunk"]
    assert document.status == DocumentStatus.COMPLETED


def test_wrong_embedding_dimension_fails_before_replacing_chunks(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Original content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Original chunk"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384],
        ),
    ):
        process_document(db_session, document.id, tenant.id)

    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Replacement content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Replacement chunk"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 12],
        ),
    ):
        with pytest.raises(ValueError, match="384 values"):
            process_document(db_session, document.id, tenant.id)

    saved_chunks = db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all()
    assert [chunk.content for chunk in saved_chunks] == ["Original chunk"]


def test_embedding_count_mismatch_fails_before_replacing_chunks(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Text",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["one", "two"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384],
        ),
    ):
        with pytest.raises(ValueError, match="count does not match"):
            process_document(db_session, document.id, tenant.id)

    assert db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all() == []


def test_database_failure_during_replacement_rolls_back_old_chunks(
    db_session: Session,
    tenant: Tenant,
    document: Document,
) -> None:
    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Original content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Original chunk"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[0.0] * 384],
        ),
    ):
        process_document(db_session, document.id, tenant.id)

    with (
        patch(
            "app.services.document_pipeline.extract_text",
            return_value="Replacement content",
        ),
        patch(
            "app.services.document_pipeline.chunk_text",
            return_value=["Replacement chunk"],
        ),
        patch(
            "app.services.document_pipeline.generate_embeddings",
            return_value=[[1.0] * 384],
        ),
        patch(
            "app.services.document_pipeline.save_document_chunks",
            side_effect=RuntimeError("database write failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="database write failed"):
            process_document(db_session, document.id, tenant.id)

    saved_chunks = db_session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    ).all()
    assert [chunk.content for chunk in saved_chunks] == ["Original chunk"]
