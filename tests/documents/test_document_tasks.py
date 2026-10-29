from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.tenants import Tenant
from app.services.document_pipeline import PreparedDocument
from app.tasks.document_tasks import (
    process_document_task,
    reconcile_stuck_processing_documents,
)


def make_session(claimed=True):
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.execute.return_value.rowcount = 1 if claimed else 0
    session.execute.return_value.one.return_value = (
        "stored.txt",
        "text/plain",
        datetime.now(timezone.utc),
    )
    return session


def prepared_document():
    return PreparedDocument(
        chunks=["a chunk"],
        embeddings=[[0.0] * 384],
        chunk_size=1000,
        chunk_overlap=150,
        embedding_model="test-model",
    )


def test_process_document_task_uses_short_claim_and_persist_sessions():
    document_id = str(uuid4())
    tenant_id = str(uuid4())
    claim_session = make_session()
    persist_session = make_session()

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            side_effect=[claim_session, persist_session],
        ),
        patch(
            "app.tasks.document_tasks.prepare_document",
            return_value=prepared_document(),
        ) as mock_prepare,
        patch(
            "app.tasks.document_tasks.persist_prepared_document",
            return_value=3,
        ) as mock_persist,
    ):
        result = process_document_task.run(document_id, tenant_id)

    mock_prepare.assert_called_once_with("stored.txt", "text/plain")
    mock_persist.assert_called_once()
    persist_kwargs = mock_persist.call_args.kwargs
    assert persist_kwargs["db"] is persist_session
    assert persist_kwargs["document_id"] == UUID(document_id)
    assert persist_kwargs["tenant_id"] == UUID(tenant_id)
    assert persist_kwargs["prepared"] == prepared_document()
    assert isinstance(persist_kwargs["claim_id"], UUID)
    claim_session.commit.assert_called_once()
    persist_session.commit.assert_called_once()
    claim_session.__exit__.assert_called_once()
    persist_session.__exit__.assert_called_once()
    assert result == {
        "document_id": document_id,
        "status": "completed",
        "chunks_created": 3,
    }


def test_processing_failure_marks_only_the_active_claim_failed():
    document_id = str(uuid4())
    tenant_id = str(uuid4())
    claim_session = make_session()
    failure_session = make_session()

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            side_effect=[claim_session, failure_session],
        ),
        patch(
            "app.tasks.document_tasks.prepare_document",
            side_effect=RuntimeError("Text extraction failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="Text extraction failed"):
            process_document_task.run(document_id, tenant_id)

    failure_update = str(failure_session.execute.call_args.args[0])
    assert "documents.tenant_id" in failure_update
    assert "documents.status" in failure_update
    assert "documents.processing_claim_id" in failure_update
    assert failure_session.commit.call_count == 1


def test_failure_status_update_does_not_replace_original_error():
    document_id = str(uuid4())
    tenant_id = str(uuid4())
    claim_session = make_session()
    failure_session = make_session()
    failure_session.execute.side_effect = RuntimeError(
        "Database unavailable"
    )

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            side_effect=[claim_session, failure_session],
        ),
        patch(
            "app.tasks.document_tasks.prepare_document",
            side_effect=ValueError("Invalid document"),
        ),
    ):
        with pytest.raises(ValueError, match="Invalid document"):
            process_document_task.run(document_id, tenant_id)

    failure_session.rollback.assert_called_once()


def test_duplicate_delivery_does_not_reprocess_document():
    document_id = str(uuid4())
    tenant_id = str(uuid4())
    claim_session = make_session(claimed=False)

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            return_value=claim_session,
        ),
        patch(
            "app.tasks.document_tasks.prepare_document",
        ) as mock_prepare,
        patch(
            "app.tasks.document_tasks.persist_prepared_document",
        ) as mock_persist,
    ):
        result = process_document_task.run(document_id, tenant_id)

    mock_prepare.assert_not_called()
    mock_persist.assert_not_called()
    claim_session.rollback.assert_called_once()
    assert result == {
        "document_id": document_id,
        "status": "skipped",
        "chunks_created": 0,
    }


def test_stale_worker_cannot_mark_newer_claim_failed():
    document_id = str(uuid4())
    tenant_id = str(uuid4())
    claim_session = make_session()
    failure_session = make_session()

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            side_effect=[claim_session, failure_session],
        ),
        patch(
            "app.tasks.document_tasks.prepare_document",
            side_effect=RuntimeError("old attempt failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="old attempt failed"):
            process_document_task.run(document_id, tenant_id)

    failure_statement = str(failure_session.execute.call_args.args[0])
    assert "documents.processing_claim_id" in failure_statement


def test_reconcile_stuck_documents_commits_and_returns_count():
    session = make_session()
    session.execute.return_value.rowcount = 2

    with patch(
        "app.tasks.document_tasks.LocalSession",
        return_value=session,
    ):
        count = reconcile_stuck_processing_documents.run()

    assert count == 2
    session.commit.assert_called_once()


def test_reconcile_failure_rolls_back_and_propagates():
    session = make_session()
    session.execute.side_effect = RuntimeError("database down")

    with patch(
        "app.tasks.document_tasks.LocalSession",
        return_value=session,
    ):
        with pytest.raises(RuntimeError, match="database down"):
            reconcile_stuck_processing_documents.run()

    session.rollback.assert_called_once()


def test_worker_claims_completes_and_skips_duplicate_delivery(
    db_session,
    monkeypatch,
):
    tenant = Tenant(name=f"worker-{uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    document = Document(
        tenant_id=tenant.id,
        filename="worker.txt",
        storage_key="worker.txt",
        content_type="text/plain",
        status=DocumentStatus.PENDING,
    )
    db_session.add(document)
    db_session.flush()

    worker_sessions = sessionmaker(
        bind=db_session.connection(),
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(
        "app.tasks.document_tasks.LocalSession",
        worker_sessions,
    )
    monkeypatch.setattr(
        "app.tasks.document_tasks.prepare_document",
        lambda storage_key, content_type: prepared_document(),
    )

    first = process_document_task.run(str(document.id), str(tenant.id))
    db_session.expire_all()
    refreshed_document = db_session.get(Document, document.id)
    saved_chunks = db_session.query(DocumentChunk).filter_by(
        document_id=document.id
    ).all()
    second = process_document_task.run(str(document.id), str(tenant.id))

    assert first["status"] == "completed"
    assert refreshed_document.status == DocumentStatus.COMPLETED
    assert refreshed_document.processing_claim_id is None
    assert len(saved_chunks) == 1
    assert saved_chunks[0].chunk_index == 0
    assert second["status"] == "skipped"


def test_stuck_worker_claim_is_reconciled_and_document_can_be_retried(
    db_session,
    monkeypatch,
):
    tenant = Tenant(name=f"reconcile-{uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    document = Document(
        tenant_id=tenant.id,
        filename="stuck.txt",
        storage_key="stuck.txt",
        content_type="text/plain",
        status=DocumentStatus.PROCESSING,
        processing_claimed_at=datetime.now(timezone.utc)
        - timedelta(hours=2),
        processing_claim_id=uuid4(),
    )
    db_session.add(document)
    db_session.flush()

    worker_sessions = sessionmaker(
        bind=db_session.connection(),
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(
        "app.tasks.document_tasks.LocalSession",
        worker_sessions,
    )

    recovered_count = reconcile_stuck_processing_documents.run()
    db_session.expire_all()
    assert db_session.get(Document, document.id).status == DocumentStatus.FAILED
    assert recovered_count == 1

    db_session.execute(
        update(Document)
        .where(Document.id == document.id)
        .values(status=DocumentStatus.PENDING)
    )
    db_session.flush()
    monkeypatch.setattr(
        "app.tasks.document_tasks.prepare_document",
        lambda storage_key, content_type: prepared_document(),
    )
    result = process_document_task.run(str(document.id), str(tenant.id))
    db_session.expire_all()

    assert result["status"] == "completed"
    assert db_session.get(Document, document.id).status == DocumentStatus.COMPLETED
