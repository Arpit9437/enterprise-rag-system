
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password, create_access_token
from app.db.session import get_db
from app.main import app
from app.models.document import Document, DocumentStatus
from app.models.tenants import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.users import User
from app.tasks.document_tasks import process_document_task


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


def create_user(db_session):
    user = User(
        email=f"{uuid.uuid4()}@example.com",
        password_hash=hash_password("TestPassword123!"),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def create_tenant(db_session, owner):
    tenant = Tenant(name=f"tenant-{uuid.uuid4()}")
    db_session.add(tenant)
    db_session.flush()

    membership = TenantMembership(
        user_id=owner.id,
        tenant_id=tenant.id,
        role="admin",
    )
    db_session.add(membership)
    db_session.flush()
    return tenant


def auth_headers(user):
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def test_member_can_create_document(client, db_session):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.post(
        f"/tenants/{tenant.id}/documents",
        json={"filename": "handbook.pdf"},
        headers=auth_headers(user),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "handbook.pdf"
    assert body["tenant_id"] == str(tenant.id)
    assert body["status"] == "pending"
    assert body["content_type"] is None
    assert "storage_key" not in body

    document = db_session.scalar(
        select(Document).where(Document.id == body["id"])
    )
    assert document is not None
    assert document.storage_key is None
    assert document.status.value == "pending"


def test_member_can_list_documents(client, db_session):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    db_session.add_all([
        Document(tenant_id=tenant.id, filename="one.pdf"),
        Document(tenant_id=tenant.id, filename="two.pdf"),
    ])
    db_session.flush()

    response = client.get(
        f"/tenants/{tenant.id}/documents",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    assert len(response.json()["documents"]) == 2


def test_document_list_is_paginated_stably_and_tenant_scoped(
    client,
    db_session,
):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)
    other_user = create_user(db_session)
    other_tenant = create_tenant(db_session, other_user)

    documents = [
        Document(tenant_id=tenant.id, filename=f"page-{index}.txt")
        for index in range(3)
    ]
    db_session.add_all(
        documents
        + [Document(tenant_id=other_tenant.id, filename="private.txt")]
    )
    db_session.flush()

    headers = auth_headers(user)
    first_page = client.get(
        f"/tenants/{tenant.id}/documents?limit=2&offset=0",
        headers=headers,
    )
    second_page = client.get(
        f"/tenants/{tenant.id}/documents?limit=2&offset=2",
        headers=headers,
    )

    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert first_page.json()["limit"] == 2
    assert first_page.json()["offset"] == 0
    assert second_page.json()["total"] == 3
    page_ids = [
        item["id"]
        for item in first_page.json()["documents"]
        + second_page.json()["documents"]
    ]
    assert len(page_ids) == len(set(page_ids)) == 3
    assert {
        item["tenant_id"]
        for item in (
            first_page.json()["documents"]
            + second_page.json()["documents"]
        )
    } == {str(tenant.id)}
    assert all(
        "storage_key" not in item
        for item in first_page.json()["documents"]
    )


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "offset=-1"],
)
def test_document_list_rejects_invalid_pagination(
    client,
    db_session,
    query,
):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.get(
        f"/tenants/{tenant.id}/documents?{query}",
        headers=auth_headers(user),
    )

    assert response.status_code == 422


def test_document_detail_is_tenant_scoped(client, db_session):
    owner_a = create_user(db_session)
    owner_b = create_user(db_session)
    tenant_a = create_tenant(db_session, owner_a)
    tenant_b = create_tenant(db_session, owner_b)
    document_b = Document(
        tenant_id=tenant_b.id,
        filename="tenant-b.txt",
    )
    db_session.add(document_b)
    db_session.flush()

    response = client.get(
        f"/tenants/{tenant_a.id}/documents/{document_b.id}",
        headers=auth_headers(owner_a),
    )

    assert response.status_code == 404


def test_document_detail_exposes_status_without_internal_storage_key(
    client,
    db_session,
):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)
    document = Document(
        tenant_id=tenant.id,
        filename="failed.txt",
        storage_key="internal-generated-key.txt",
        content_type="text/plain",
        status=DocumentStatus.FAILED,
    )
    db_session.add(document)
    db_session.flush()

    response = client.get(
        f"/tenants/{tenant.id}/documents/{document.id}",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "storage_key" not in response.json()


def test_deleting_pending_document_prevents_worker_claim(
    client,
    db_session,
):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)
    document = Document(
        tenant_id=tenant.id,
        filename="pending.txt",
        storage_key=f"{uuid.uuid4().hex}.txt",
        content_type="text/plain",
        status=DocumentStatus.PENDING,
    )
    db_session.add(document)
    db_session.flush()
    document_id = document.id

    deleted = client.delete(
        f"/tenants/{tenant.id}/documents/{document_id}",
        headers=auth_headers(user),
    )
    assert deleted.status_code == 204
    assert db_session.get(Document, document_id) is None

    worker_sessions = sessionmaker(
        bind=db_session.connection(),
        join_transaction_mode="create_savepoint",
    )
    with (
        patch(
            "app.tasks.document_tasks.LocalSession",
            worker_sessions,
        ),
        patch(
            "app.tasks.document_tasks.prepare_document",
        ) as mock_prepare,
    ):
        result = process_document_task.run(
            str(document_id),
            str(tenant.id),
        )

    assert result["status"] == "skipped"
    mock_prepare.assert_not_called()


def test_non_member_cannot_list_documents(client, db_session):
    owner = create_user(db_session)
    outsider = create_user(db_session)
    tenant = create_tenant(db_session, owner)

    response = client.get(
        f"/tenants/{tenant.id}/documents",
        headers=auth_headers(outsider),
    )

    assert response.status_code == 404


def test_non_member_cannot_create_document(client, db_session):
    owner = create_user(db_session)
    outsider = create_user(db_session)
    tenant = create_tenant(db_session, owner)

    response = client.post(
        f"/tenants/{tenant.id}/documents",
        json={"filename": "private.pdf"},
        headers=auth_headers(outsider),
    )

    assert response.status_code == 404


def test_document_creation_requires_authentication(client, db_session):
    owner = create_user(db_session)
    tenant = create_tenant(db_session, owner)

    response = client.post(
        f"/tenants/{tenant.id}/documents",
        json={"filename": "private.pdf"},
    )

    assert response.status_code == 401


def test_blank_filename_is_rejected(client, db_session):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.post(
        f"/tenants/{tenant.id}/documents",
        json={"filename": "   "},
        headers=auth_headers(user),
    )

    assert response.status_code == 422
