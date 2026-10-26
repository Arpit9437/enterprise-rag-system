
from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.services import document_processing


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        document_processing.settings,
        "document_storage_dir",
        tmp_path,
    )
    return tmp_path


def test_extracts_utf8_text_file(storage_dir):
    content = "Company policy: all documents must be reviewed."
    (storage_dir / "sample.txt").write_text(
        content,
        encoding="utf-8",
    )

    result = document_processing.extract_text(
        "sample.txt",
        "text/plain",
    )

    assert result == content


def test_extracts_pdf_text(storage_dir):
    # A blank PDF verifies PDF parsing, but has no extractable text.
    # Use the empty-text behavior test below for this fixture.
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)

    output = BytesIO()
    writer.write(output)

    (storage_dir / "blank.pdf").write_bytes(output.getvalue())

    with pytest.raises(ValueError, match="No extractable text"):
        document_processing.extract_text(
            "blank.pdf",
            "application/pdf",
        )


def test_rejects_unsupported_content_type(storage_dir):
    (storage_dir / "sample.bin").write_bytes(b"data")

    with pytest.raises(ValueError, match="Unsupported"):
        document_processing.extract_text(
            "sample.bin",
            "application/octet-stream",
        )


def test_rejects_path_traversal(storage_dir):
    with pytest.raises(ValueError, match="Invalid storage key"):
        document_processing.extract_text(
            "../secret.txt",
            "text/plain",
        )


def test_rejects_missing_file(storage_dir):
    with pytest.raises(FileNotFoundError):
        document_processing.extract_text(
            "missing.txt",
            "text/plain",
        )


def test_chunk_text_returns_one_chunk_for_short_text():
    text = "A short document."

    chunks = document_processing.chunk_text(text)

    assert chunks == [text]


def test_chunk_text_splits_long_text_with_overlap():
    text = "abcdefghijklmnopqrstuvwxyz"

    chunks = document_processing.chunk_text(
        text,
        chunk_size=10,
        overlap=3,
    )

    assert chunks == [
        "abcdefghij",
        "hijklmnopq",
        "opqrstuvwx",
        "vwxyz",
    ]


def test_chunk_text_handles_empty_input():
    assert document_processing.chunk_text("") == []
    assert document_processing.chunk_text("   ") == []


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [
        (0, 0),
        (-1, 0),
        (10, -1),
        (10, 10),
        (10, 11),
    ],
)
def test_chunk_text_rejects_invalid_parameters(chunk_size, overlap):
    with pytest.raises(ValueError):
        document_processing.chunk_text(
            "some text",
            chunk_size=chunk_size,
            overlap=overlap,
        )


def test_chunk_text_preserves_all_characters_across_chunks():
    text = "0123456789ABCDEFGHIJ"

    chunks = document_processing.chunk_text(
        text,
        chunk_size=8,
        overlap=2,
    )

    # Each next chunk begins with the previous chunk's overlap.
    for previous, current in zip(chunks, chunks[1:]):
        assert previous[-2:] == current[:2]

    # Removing overlap reconstructs the original text.
    reconstructed = chunks[0] + "".join(
        chunk[2:] for chunk in chunks[1:]
    )
    assert reconstructed == text
