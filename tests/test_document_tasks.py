from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.document import DocumentStatus
from app.tasks.document_tasks import process_document_task


def make_session():
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    return session


def test_process_document_task_commits_successful_processing():
    document_id = str(uuid4())
    tenant_id = str(uuid4())
    session = make_session()

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            return_value=session,
        ),
        patch(
            "app.tasks.document_tasks.process_document",
            return_value=3,
        ) as mock_process,
    ):
        result = process_document_task.run(document_id, tenant_id)

    mock_process.assert_called_once_with(
        db=session,
        document_id=__import__("uuid").UUID(document_id),
        tenant_id=__import__("uuid").UUID(tenant_id),
    )
    session.commit.assert_called_once()

    assert result == {
        "document_id": document_id,
        "status": "completed",
        "chunks_created": 3,
    }


def test_process_document_task_marks_document_failed():
    document_id = str(uuid4())
    tenant_id = str(uuid4())

    processing_session = make_session()
    failure_session = make_session()

    failed_document = MagicMock()
    failure_session.scalar.return_value = failed_document

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            side_effect=[processing_session, failure_session],
        ),
        patch(
            "app.tasks.document_tasks.process_document",
            side_effect=RuntimeError("Text extraction failed"),
        ),
    ):
        with pytest.raises(
            RuntimeError,
            match="Text extraction failed",
        ):
            process_document_task.run(document_id, tenant_id)

    assert failed_document.status == DocumentStatus.FAILED
    failure_session.commit.assert_called_once()


def test_process_document_task_preserves_error_if_failure_status_update_fails():
    document_id = str(uuid4())
    tenant_id = str(uuid4())

    processing_session = make_session()
    failure_session = make_session()
    failure_session.scalar.side_effect = RuntimeError(
        "Database unavailable"
    )

    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            side_effect=[processing_session, failure_session],
        ),
        patch(
            "app.tasks.document_tasks.process_document",
            side_effect=ValueError("Invalid document"),
        ),
    ):
        with pytest.raises(ValueError, match="Invalid document"):
            process_document_task.run(document_id, tenant_id)

    failure_session.rollback.assert_called_once()
