
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from app.api.routes.documents import upload_document
from app.models.document import DocumentStatus


def make_upload_file(filename: str = "guide.txt") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=BytesIO(b"test document"),
    )


def make_db_session(document_id):
    db = MagicMock()

    # SQLAlchemy normally assigns Python-side defaults during flush.
    # Simulate that behavior for the mocked session.
    def assign_document_id():
        document = db.add.call_args.args[0]
        document.id = document_id

    db.flush.side_effect = assign_document_id
    return db


@pytest.mark.asyncio
async def test_upload_commits_document_before_enqueueing_task():
    tenant_id = uuid4()
    document_id = uuid4()
    user = MagicMock()
    db = make_db_session(document_id)
    file = make_upload_file()

    event_order = []

    def commit():
        event_order.append("commit")

    def enqueue(document_id_arg, tenant_id_arg):
        event_order.append("enqueue")
        assert document_id_arg == str(document_id)
        assert tenant_id_arg == str(tenant_id)

    db.commit.side_effect = commit

    with (
        patch(
            "app.api.routes.documents.store_uploaded_file",
            new_callable=AsyncMock,
            return_value=("stored-file.txt", "text/plain"),
        ),
        patch(
            "app.api.routes.documents.process_document_task.delay",
            side_effect=enqueue,
        ) as mock_delay,
    ):
        result = await upload_document(
            tenant_id=tenant_id,
            current_user=user,
            db=db,
            file=file,
        )

    assert result.id == document_id
    assert result.tenant_id == tenant_id
    assert result.filename == "guide.txt"
    assert result.storage_key == "stored-file.txt"
    assert result.content_type == "text/plain"
    assert result.status == DocumentStatus.PENDING

    assert event_order == ["commit", "enqueue"]
    db.commit.assert_called_once()
    mock_delay.assert_called_once_with(
        str(document_id),
        str(tenant_id),
    )


@pytest.mark.asyncio
async def test_upload_keeps_document_and_file_if_enqueue_fails():
    tenant_id = uuid4()
    document_id = uuid4()
    db = make_db_session(document_id)
    file = make_upload_file()

    with (
        patch(
            "app.api.routes.documents.store_uploaded_file",
            new_callable=AsyncMock,
            return_value=("stored-file.txt", "text/plain"),
        ),
        patch(
            "app.api.routes.documents.process_document_task.delay",
            side_effect=ConnectionError("Redis unavailable"),
        ),
        patch(
            "app.api.routes.documents.delete_stored_file",
        ) as mock_delete,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await upload_document(
                tenant_id=tenant_id,
                current_user=MagicMock(),
                db=db,
                file=file,
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["document_id"] == str(document_id)
    assert exc_info.value.detail["status"] == DocumentStatus.PENDING.value

    # The database commit already succeeded.
    db.commit.assert_called_once()

    # Do not attempt to undo the committed document or delete its file.
    db.rollback.assert_not_called()
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_upload_rolls_back_and_deletes_file_if_database_fails():
    tenant_id = uuid4()
    document_id = uuid4()
    db = make_db_session(document_id)
    db.commit.side_effect = RuntimeError("Database commit failed")

    with (
        patch(
            "app.api.routes.documents.store_uploaded_file",
            new_callable=AsyncMock,
            return_value=("stored-file.txt", "text/plain"),
        ),
        patch(
            "app.api.routes.documents.process_document_task.delay",
        ) as mock_delay,
        patch(
            "app.api.routes.documents.delete_stored_file",
        ) as mock_delete,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await upload_document(
                tenant_id=tenant_id,
                current_user=MagicMock(),
                db=db,
                file=make_upload_file(),
            )

    assert exc_info.value.status_code == 500
    db.rollback.assert_called_once()
    mock_delete.assert_called_once_with("stored-file.txt")
    mock_delay.assert_not_called()


@pytest.mark.asyncio
async def test_upload_rejects_blank_filename_without_storing_file():
    tenant_id = uuid4()
    db = MagicMock()
    file = make_upload_file(filename="   ")

    with patch(
        "app.api.routes.documents.store_uploaded_file",
        new_callable=AsyncMock,
    ) as mock_store:
        with pytest.raises(HTTPException) as exc_info:
            await upload_document(
                tenant_id=tenant_id,
                current_user=MagicMock(),
                db=db,
                file=file,
            )

    assert exc_info.value.status_code == 422
    mock_store.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()
