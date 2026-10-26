
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password, create_access_token
from app.db.session import get_db
from app.main import app
from app.models.document import Document
from app.models.tenants import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.users import User


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

    document = db_session.scalar(
        select(Document).where(Document.id == body["id"])
    )
    assert document is not None


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
