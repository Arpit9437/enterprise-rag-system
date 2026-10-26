
from pathlib import Path

from pypdf import PdfReader

from app.core.config import settings


CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


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
        text = file_path.read_text(encoding="utf-8")

    elif content_type == "application/pdf":
        reader = PdfReader(str(file_path), strict=True)

        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")

        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
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
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """
    Split text into character-based chunks with overlap.

    The overlap preserves some context between adjacent chunks.
    """
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
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        start = end - overlap

    return chunks
