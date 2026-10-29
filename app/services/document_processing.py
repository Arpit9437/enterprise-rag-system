
from pathlib import Path

from pypdf import PdfReader

from app.core.config import settings


def extract_text(storage_key: str, content_type: str) -> str:
    """
    Extract text from a stored PDF or UTF-8 text file.

    storage_key must be a generated storage identifier, not a user path.
    """
    if Path(storage_key).name != storage_key:
        raise ValueError("Invalid storage key")

    storage_dir = Path(settings.document_storage_dir).resolve()
    file_path = (storage_dir / storage_key).resolve()

    if file_path.parent != storage_dir:
        raise ValueError("Invalid storage path")

    if not file_path.is_file():
        raise FileNotFoundError("Stored document not found")

    if content_type == "text/plain":
        with file_path.open("r", encoding="utf-8") as stored_file:
            text = stored_file.read(settings.max_document_text_chars + 1)
        if len(text) > settings.max_document_text_chars:
            raise ValueError("Document text exceeds the configured limit")

    elif content_type == "application/pdf":
        reader = PdfReader(str(file_path), strict=True)

        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")

        pages = []
        total_chars = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                total_chars += len(page_text)
                if total_chars > settings.max_document_text_chars:
                    raise ValueError(
                        "Document text exceeds the configured limit"
                    )
                pages.append(page_text)

        text = "\n\n".join(pages)

    else:
        raise ValueError("Unsupported document content type")

    text = text.replace("\x00", "").strip()

    if not text:
        raise ValueError("No extractable text found in document")

    return text


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """
    Split text into character-based chunks with overlap.

    The overlap preserves some context between adjacent chunks.
    """
    chunk_size = (
        settings.document_chunk_size if chunk_size is None else chunk_size
    )
    overlap = (
        settings.document_chunk_overlap if overlap is None else overlap
    )

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = max(
                (
                    index
                    for index in range(start + chunk_size // 2, end)
                    if text[index].isspace()
                ),
                default=-1,
            )
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        next_start = end - overlap
        while next_start < end and text[next_start].isspace():
            next_start += 1
        start = max(start + 1, next_start)

    return chunks
