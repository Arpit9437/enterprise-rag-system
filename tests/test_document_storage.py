from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services import document_storage


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        document_storage.settings,
        "document_storage_dir",
        tmp_path,
    )
    monkeypatch.setattr(
        document_storage.settings,
        "max_document_size_bytes",
        10 * 1024 * 1024,
    )
    return tmp_path


def make_upload(
    filename: str,
    content: bytes,
    content_type: str,
) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


@pytest.mark.anyio
async def test_stores_valid_pdf(storage_dir):
    upload = make_upload(
        "guide.pdf",
        b"%PDF-1.7\nexample pdf content",
        "application/pdf",
    )

    storage_key, content_type = (
        await document_storage.store_uploaded_file(upload)
    )

    saved_file = storage_dir / storage_key
    assert saved_file.exists()
    assert saved_file.read_bytes().startswith(b"%PDF-")
    assert content_type == "application/pdf"
    assert storage_key != "guide.pdf"


@pytest.mark.anyio
async def test_stores_valid_utf8_text(storage_dir):
    content = "RAG knowledge base".encode("utf-8")
    upload = make_upload("notes.txt", content, "text/plain")

    storage_key, content_type = (
        await document_storage.store_uploaded_file(upload)
    )

    assert (storage_dir / storage_key).read_bytes() == content
    assert content_type == "text/plain"


@pytest.mark.anyio
async def test_rejects_unsupported_extension(storage_dir):
    upload = make_upload(
        "script.py",
        b"print('hello')",
        "text/plain",
    )

    with pytest.raises(HTTPException) as exc:
        await document_storage.store_uploaded_file(upload)

    assert exc.value.status_code == 415
    assert list(storage_dir.iterdir()) == []


@pytest.mark.anyio
async def test_rejects_mismatched_content_type(storage_dir):
    upload = make_upload(
        "guide.pdf",
        b"%PDF-1.7\ncontent",
        "text/plain",
    )

    with pytest.raises(HTTPException) as exc:
        await document_storage.store_uploaded_file(upload)

    assert exc.value.status_code == 415
    assert list(storage_dir.iterdir()) == []


@pytest.mark.anyio
async def test_rejects_invalid_pdf_and_cleans_up(storage_dir):
    upload = make_upload(
        "guide.pdf",
        b"not a pdf",
        "application/pdf",
    )

    with pytest.raises(HTTPException) as exc:
        await document_storage.store_uploaded_file(upload)

    assert exc.value.status_code == 422
    assert list(storage_dir.iterdir()) == []


@pytest.mark.anyio
async def test_rejects_oversized_file_and_cleans_up(
    storage_dir,
    monkeypatch,
):
    monkeypatch.setattr(
        document_storage.settings,
        "max_document_size_bytes",
        8,
    )
    upload = make_upload(
        "notes.txt",
        b"this is larger than eight bytes",
        "text/plain",
    )

    with pytest.raises(HTTPException) as exc:
        await document_storage.store_uploaded_file(upload)

    assert exc.value.status_code == 413
    assert list(storage_dir.iterdir()) == []


def test_delete_stored_file(storage_dir):
    storage_key = "generated-key.txt"
    (storage_dir / storage_key).write_text("content", encoding="utf-8")

    document_storage.delete_stored_file(storage_key)

    assert not (storage_dir / storage_key).exists()


def test_delete_rejects_path_traversal(storage_dir):
    with pytest.raises(ValueError):
        document_storage.delete_stored_file("../secret.txt")
