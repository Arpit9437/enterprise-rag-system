
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}

CHUNK_SIZE = 1024 * 1024  # 1 MB


def _validate_file_metadata(file: UploadFile) -> str:
    filename = (file.filename or "").strip()

    if not filename or filename in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A valid filename is required",
        )

    suffix = Path(filename).suffix.lower()
    expected_suffix = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }

    if suffix not in expected_suffix:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and TXT files are supported",
        )

    content_type = file.content_type or ""
    if content_type != expected_suffix[suffix]:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File extension and content type do not match",
        )

    return content_type


async def store_uploaded_file(file: UploadFile) -> tuple[str, str]:
    content_type = _validate_file_metadata(file)
    storage_dir = Path(settings.document_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    storage_key = f"{uuid4().hex}{ALLOWED_CONTENT_TYPES[content_type]}"
    destination = storage_dir / storage_key
    total_size = 0
    prefix = b""

    try:
        with destination.open("xb") as output:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break

                total_size += len(chunk)
                if total_size > settings.max_document_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="File exceeds the 10 MB size limit",
                    )

                if len(prefix) < 8:
                    prefix += chunk[: 8 - len(prefix)]

                output.write(chunk)

        if content_type == "application/pdf" and not prefix.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded file does not appear to be a valid PDF",
            )

        if content_type == "text/plain":
            try:
                with destination.open("rb") as stored_file:
                    for line in stored_file:
                        line.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Text files must be valid UTF-8",
                )

        return storage_key, content_type

    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def delete_stored_file(storage_key: str) -> None:
    """Delete a stored file using its generated storage key."""
    if Path(storage_key).name != storage_key:
        raise ValueError("Invalid storage key")

    storage_dir = Path(settings.document_storage_dir).resolve()
    destination = (storage_dir / storage_key).resolve()

    if destination.parent != storage_dir:
        raise ValueError("Invalid storage path")

    destination.unlink(missing_ok=True)
