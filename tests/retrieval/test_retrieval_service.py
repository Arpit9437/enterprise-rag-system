import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk, EMBEDDING_DIMENSIONS
from app.models.tenants import Tenant
from app.services.embedding_service import MODEL_NAME
from app.services.retrieval_service import semantic_search


def make_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


@pytest.fixture
def tenant(db_session: Session) -> Tenant:
    item = Tenant(name=f"retrieval-test-{uuid.uuid4().hex}")
    db_session.add(item)
    db_session.flush()
    return item


def add_document(
    db_session: Session,
    tenant_id,
    filename: str,
    status: DocumentStatus = DocumentStatus.COMPLETED,
) -> Document:
    document = Document(
        tenant_id=tenant_id,
        filename=filename,
        storage_key=f"{uuid.uuid4().hex}.txt",
        content_type="text/plain",
        status=status,
    )
    db_session.add(document)
    db_session.flush()
    return document


def add_chunk(
    db_session: Session,
    document: Document,
    index: int,
    content: str,
    embedding: list[float] | None,
) -> DocumentChunk:
    chunk = DocumentChunk(
        tenant_id=document.tenant_id,
        document_id=document.id,
        chunk_index=index,
        content=content,
        embedding=embedding,
        embedding_model=MODEL_NAME if embedding is not None else None,
    )
    db_session.add(chunk)
    db_session.flush()
    return chunk


def test_semantic_search_ranks_closest_chunks_first(
    db_session: Session,
    tenant: Tenant,
) -> None:
    document = add_document(db_session, tenant.id, "policies.txt")

    closest = add_chunk(
        db_session, document, 0, "Notice period is 30 days.", make_vector(0)
    )
    add_chunk(
        db_session, document, 1, "The office is in Mumbai.", make_vector(1)
    )

    with patch(
        "app.services.retrieval_service.generate_embeddings",
        return_value=[make_vector(0)],
    ):
        results = semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="What is the notice period?",
            top_k=2,
        )

    assert len(results) == 2
    assert results[0].chunk_id == closest.id
    assert results[0].content == "Notice period is 30 days."
    assert results[0].cosine_distance == pytest.approx(0.0)
    assert results[0].similarity_score == pytest.approx(1.0)
    assert results[0].cosine_distance <= results[1].cosine_distance


def test_semantic_search_does_not_return_another_tenants_chunks(
    db_session: Session,
    tenant: Tenant,
) -> None:
    other_tenant = Tenant(name=f"other-tenant-{uuid.uuid4().hex}")
    db_session.add(other_tenant)
    db_session.flush()

    own_document = add_document(db_session, tenant.id, "own.txt")
    foreign_document = add_document(db_session, other_tenant.id, "foreign.txt")

    add_chunk(
        db_session, own_document, 0, "Visible to this tenant.", make_vector(0)
    )
    add_chunk(
        db_session,
        foreign_document,
        0,
        "Private content from another tenant.",
        make_vector(0),
    )

    with patch(
        "app.services.retrieval_service.generate_embeddings",
        return_value=[make_vector(0)],
    ):
        results = semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="Find relevant content",
        )

    assert results
    assert all(result.document_id == own_document.id for result in results)
    assert all(
        "Private content from another tenant." not in result.content
        for result in results
    )


def test_semantic_search_excludes_incomplete_documents(
    db_session: Session,
    tenant: Tenant,
) -> None:
    pending_document = add_document(
        db_session,
        tenant.id,
        "pending.txt",
        status=DocumentStatus.PENDING,
    )
    add_chunk(
        db_session,
        pending_document,
        0,
        "This document is not ready.",
        make_vector(0),
    )

    with patch(
        "app.services.retrieval_service.generate_embeddings",
        return_value=[make_vector(0)],
    ):
        results = semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="Find content",
        )

    assert results == []


def test_semantic_search_applies_document_and_content_type_filters(
    db_session: Session,
    tenant: Tenant,
) -> None:
    matching = add_document(db_session, tenant.id, "matching.txt")
    excluded = add_document(db_session, tenant.id, "excluded.txt")
    add_chunk(
        db_session,
        matching,
        0,
        "Matching content",
        make_vector(0),
    )
    add_chunk(
        db_session,
        excluded,
        0,
        "Excluded content",
        make_vector(0),
    )

    with patch(
        "app.services.retrieval_service.generate_embeddings",
        return_value=[make_vector(0)],
    ):
        results = semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="Matching",
            document_ids=[matching.id],
            content_type="text/plain",
        )

    assert [result.document_id for result in results] == [matching.id]


def test_semantic_search_empty_document_filter_returns_no_results(
    db_session: Session,
    tenant: Tenant,
) -> None:
    with patch(
        "app.services.retrieval_service.generate_embeddings"
    ) as mock_embeddings:
        results = semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="Anything",
            document_ids=[],
        )

    assert results == []
    mock_embeddings.assert_not_called()


def test_semantic_search_returns_empty_list_when_no_chunks_match(
    db_session: Session,
    tenant: Tenant,
) -> None:
    with patch(
        "app.services.retrieval_service.generate_embeddings",
        return_value=[make_vector(0)],
    ):
        results = semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="Anything relevant?",
        )

    assert results == []


def test_semantic_search_rejects_blank_query(
    db_session: Session,
    tenant: Tenant,
) -> None:
    with patch(
        "app.services.retrieval_service.generate_embeddings"
    ) as mock_embeddings:
        with pytest.raises(ValueError, match="cannot be blank"):
            semantic_search(db_session, tenant.id, "   ")

    mock_embeddings.assert_not_called()


@pytest.mark.parametrize("top_k", [0, -1, 21])
def test_semantic_search_rejects_invalid_top_k(
    db_session: Session,
    tenant: Tenant,
    top_k: int,
) -> None:
    with pytest.raises(ValueError, match="top_k must be between 1 and 20"):
        semantic_search(
            db=db_session,
            tenant_id=tenant.id,
            query="Find content",
            top_k=top_k,
        )
