from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.llm_dependencies import get_llm_provider
from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.main import app
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.tenant_membership import TenantMembership
from app.models.tenants import Tenant
from app.models.users import User
from app.services.document_pipeline import process_document
from app.services.embedding_service import MODEL_NAME


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


class StaticProvider:
    def generate(self, prompt: str) -> str:
        return "Tenant A source response [Source 1]"


def create_user(db_session, name: str) -> User:
    user = User(
        email=f"{name}-{uuid4().hex}@example.com",
        password_hash=hash_password("TestPassword123!"),
    )
    db_session.add(user)
    db_session.flush()
    return user


def create_tenant(db_session, user: User, name: str) -> Tenant:
    tenant = Tenant(name=f"{name}-{uuid4().hex}")
    db_session.add(tenant)
    db_session.flush()
    db_session.add(
        TenantMembership(
            user_id=user.id,
            tenant_id=tenant.id,
            role="admin",
        )
    )
    db_session.flush()
    return tenant


def create_completed_document(db_session, tenant: Tenant, content: str):
    suffix = uuid4().hex
    document = Document(
        tenant_id=tenant.id,
        filename=f"{suffix}.txt",
        storage_key=f"{suffix}.txt",
        content_type="text/plain",
        status=DocumentStatus.COMPLETED,
    )
    db_session.add(document)
    db_session.flush()

    db_session.add(
        DocumentChunk(
            tenant_id=tenant.id,
            document_id=document.id,
            chunk_index=0,
            content=content,
            embedding=[1.0] + [0.0] * 383,
            embedding_model=MODEL_NAME,
        )
    )
    db_session.flush()
    return document


def auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def test_tenant_member_api_search_and_ask_are_isolated(
    client,
    db_session,
):
    suffix = uuid4().hex
    user_a = create_user(db_session, f"user-a-{suffix}")
    user_b = create_user(db_session, f"user-b-{suffix}")
    tenant_a = create_tenant(db_session, user_a, f"tenant-a-{suffix}")
    tenant_b = create_tenant(db_session, user_b, f"tenant-b-{suffix}")
    doc_a = create_completed_document(
        db_session,
        tenant_a,
        "universal compliance token Tenant A confidential content",
    )
    doc_b = create_completed_document(
        db_session,
        tenant_b,
        "universal compliance token Tenant B confidential content",
    )
    headers_a = auth_headers(user_a)
    app.dependency_overrides[get_llm_provider] = lambda: StaticProvider()

    list_response = client.get(
        f"/tenants/{tenant_a.id}/documents",
        headers=headers_a,
    )
    assert list_response.status_code == 200
    assert {
        item["id"] for item in list_response.json()["documents"]
    } == {str(doc_a.id)}

    search_response = client.post(
        f"/tenants/{tenant_a.id}/search",
        headers=headers_a,
        json={"query": "universal compliance token", "top_k": 5},
    )
    assert search_response.status_code == 200
    search_document_ids = {
        result["document_id"]
        for result in search_response.json()["results"]
    }
    assert search_document_ids == {str(doc_a.id)}
    assert "Tenant B confidential content" not in search_response.text

    filtered_search = client.post(
        f"/tenants/{tenant_a.id}/search",
        headers=headers_a,
        json={
            "query": "universal compliance token",
            "document_ids": [str(doc_b.id)],
        },
    )
    assert filtered_search.status_code == 200
    assert filtered_search.json()["results"] == []

    ask_response = client.post(
        f"/tenants/{tenant_a.id}/ask",
        headers=headers_a,
        json={"question": "What is in the compliance document?"},
    )
    assert ask_response.status_code == 200
    assert {
        citation["document_id"]
        for citation in ask_response.json()["citations"]
    } == {str(doc_a.id)}
    assert "Tenant B confidential content" not in ask_response.text

    for path in (
        f"/tenants/{tenant_b.id}/documents",
        f"/tenants/{tenant_b.id}/search",
        f"/tenants/{tenant_b.id}/ask",
    ):
        response = (
            client.get(path, headers=headers_a)
            if path.endswith("/documents")
            else client.post(
                path,
                headers=headers_a,
                json=(
                    {"query": "universal compliance token"}
                    if path.endswith("/search")
                    else {"question": "What is in the document?"}
                ),
            )
        )
        assert response.status_code == 404

    assert client.get(
        f"/tenants/{tenant_a.id}/documents/{doc_b.id}",
        headers=headers_a,
    ).status_code == 404
    assert client.delete(
        f"/tenants/{tenant_a.id}/documents/{doc_b.id}",
        headers=headers_a,
    ).status_code == 404
    assert db_session.get(Document, doc_b.id) is not None

    with pytest.raises(ValueError, match="Document not found"):
        process_document(
            db=db_session,
            document_id=doc_b.id,
            tenant_id=tenant_a.id,
        )
    assert db_session.get(Document, doc_b.id).status == DocumentStatus.COMPLETED


def test_tenant_search_requires_membership(client, db_session):
    owner = create_user(db_session, f"owner-{uuid4().hex}")
    outsider = create_user(db_session, f"outsider-{uuid4().hex}")
    tenant = create_tenant(db_session, owner, f"private-{uuid4().hex}")
    create_completed_document(
        db_session,
        tenant,
        "universal compliance token private content",
    )

    response = client.post(
        f"/tenants/{tenant.id}/search",
        headers=auth_headers(outsider),
        json={"query": "universal compliance token"},
    )

    assert response.status_code == 404
