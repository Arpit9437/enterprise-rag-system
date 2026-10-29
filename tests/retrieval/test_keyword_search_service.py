from uuid import uuid4

import pytest

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.tenants import Tenant
from app.services.keyword_search_service import keyword_search


def create_document_with_chunk(
    db_session,
    *,
    status: DocumentStatus = DocumentStatus.COMPLETED,
    content: str = (
        "Redis distributed lock uses SET NX EX "
        "to coordinate access between workers."
    ),
    tenant: Tenant | None = None,
) -> Document:
    """Create a tenant, document, and chunk for a keyword-search test."""

    if tenant is None:
        tenant = Tenant(name=f"keyword-search-{uuid4().hex}")
        db_session.add(tenant)
        db_session.flush()

    document = Document(
        tenant_id=tenant.id,
        filename=f"test-{uuid4().hex}.txt",
        status=status,
    )
    db_session.add(document)
    db_session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        tenant_id=tenant.id,
        chunk_index=0,
        content=content,
    )
    db_session.add(chunk)
    db_session.flush()

    return document


def test_keyword_search_returns_matching_chunk(db_session):
    document = create_document_with_chunk(db_session)

    results = keyword_search(
        db=db_session,
        tenant_id=document.tenant_id,
        query="Redis distributed lock",
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].document_id == document.id
    assert "Redis" in results[0].content
    assert results[0].keyword_score > 0


def test_keyword_search_handles_punctuation_safely(db_session):
    document = create_document_with_chunk(db_session)

    results = keyword_search(
        db=db_session,
        tenant_id=document.tenant_id,
        query="Redis (distributed) / lock!!!",
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].document_id == document.id


def test_keyword_search_returns_empty_for_no_match(db_session):
    document = create_document_with_chunk(db_session)

    results = keyword_search(
        db=db_session,
        tenant_id=document.tenant_id,
        query="xylophone astrophysics",
        top_k=5,
    )

    assert results == []


def test_keyword_search_excludes_other_tenants(db_session):
    document = create_document_with_chunk(db_session)

    other_tenant = Tenant(name=f"other-tenant-{uuid4().hex}")
    db_session.add(other_tenant)
    db_session.flush()

    results = keyword_search(
        db=db_session,
        tenant_id=other_tenant.id,
        query="Redis distributed lock",
        top_k=5,
    )

    assert results == []


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_keyword_search_rejects_blank_query(db_session, query):
    with pytest.raises(ValueError, match="blank"):
        keyword_search(
            db=db_session,
            tenant_id=uuid4(),
            query=query,
        )


@pytest.mark.parametrize("top_k", [0, -1, 21])
def test_keyword_search_rejects_invalid_top_k(db_session, top_k):
    with pytest.raises(ValueError, match="top_k"):
        keyword_search(
            db=db_session,
            tenant_id=uuid4(),
            query="Redis",
            top_k=top_k,
        )


def test_keyword_search_excludes_non_completed_documents(db_session):
    document = create_document_with_chunk(
        db_session,
        status=DocumentStatus.PENDING,
    )

    results = keyword_search(
        db=db_session,
        tenant_id=document.tenant_id,
        query="Redis distributed lock",
        top_k=5,
    )

    assert results == []
