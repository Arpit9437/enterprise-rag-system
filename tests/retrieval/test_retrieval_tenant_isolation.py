import uuid

import pytest

from app.models.tenants import Tenant
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.services.embedding_service import MODEL_NAME
from app.services.retrieval_service import semantic_search
from app.services.keyword_search_service import keyword_search
from app.services.hybrid_search_service import hybrid_search


@pytest.fixture
def tenant_search_data(db_session):
    """Create matching documents for two isolated tenants."""

    suffix = uuid.uuid4().hex

    tenant_a = Tenant(name=f"tenant-a-{suffix}")
    tenant_b = Tenant(name=f"tenant-b-{suffix}")

    db_session.add_all([tenant_a, tenant_b])
    db_session.flush()

    shared_content = (
        "Quantum zebra compliance report describes the annual "
        "security review and document processing workflow."
    )

    document_a = Document(
        tenant_id=tenant_a.id,
        filename="tenant-a.txt",
        storage_key=f"{suffix}-a.txt",
        content_type="text/plain",
        status=DocumentStatus.COMPLETED,
    )

    document_b = Document(
        tenant_id=tenant_b.id,
        filename="tenant-b.txt",
        storage_key=f"{suffix}-b.txt",
        content_type="text/plain",
        status=DocumentStatus.COMPLETED,
    )

    db_session.add_all([document_a, document_b])
    db_session.flush()

    # Nonzero vectors with the required 384 dimensions.
    embedding_a = [1.0] + [0.0] * 383
    embedding_b = [0.0, 1.0] + [0.0] * 382

    chunk_a = DocumentChunk(
        tenant_id=tenant_a.id,
        document_id=document_a.id,
        chunk_index=0,
        content=shared_content,
        embedding=embedding_a,
        embedding_model=MODEL_NAME,
    )

    chunk_b = DocumentChunk(
        tenant_id=tenant_b.id,
        document_id=document_b.id,
        chunk_index=0,
        content=shared_content,
        embedding=embedding_b,
        embedding_model=MODEL_NAME,
    )

    db_session.add_all([chunk_a, chunk_b])
    db_session.flush()

    return {
        "tenant_a_id": tenant_a.id,
        "tenant_b_id": tenant_b.id,
        "document_a_id": document_a.id,
        "document_b_id": document_b.id,
        "chunk_a_id": chunk_a.id,
        "chunk_b_id": chunk_b.id,
    }


def assert_results_belong_only_to_tenant_a(
    results,
    tenant_search_data,
):
    result_document_ids = {
        result.document_id for result in results
    }

    assert (
        tenant_search_data["document_b_id"]
        not in result_document_ids
    )

    assert result_document_ids.issubset(
        {tenant_search_data["document_a_id"]}
    )


def test_semantic_search_is_tenant_isolated(
    db_session,
    tenant_search_data,
):
    results = semantic_search(
        db=db_session,
        tenant_id=tenant_search_data["tenant_a_id"],
        query="Quantum zebra compliance report",
        top_k=20,
    )

    assert_results_belong_only_to_tenant_a(
        results,
        tenant_search_data,
    )

    assert any(
        result.document_id == tenant_search_data["document_a_id"]
        for result in results
    )


def test_keyword_search_is_tenant_isolated(
    db_session,
    tenant_search_data,
):
    results = keyword_search(
        db=db_session,
        tenant_id=tenant_search_data["tenant_a_id"],
        query="Quantum zebra compliance report",
        top_k=20,
    )

    assert_results_belong_only_to_tenant_a(
        results,
        tenant_search_data,
    )

    assert any(
        result.document_id == tenant_search_data["document_a_id"]
        for result in results
    )


def test_hybrid_search_is_tenant_isolated(
    db_session,
    tenant_search_data,
):
    results = hybrid_search(
        db=db_session,
        tenant_id=tenant_search_data["tenant_a_id"],
        query="Quantum zebra compliance report",
        top_k=20,
    )

    assert_results_belong_only_to_tenant_a(
        results,
        tenant_search_data,
    )

    assert any(
        result.document_id == tenant_search_data["document_a_id"]
        for result in results
    )
