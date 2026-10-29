
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.models.tenants import Tenant
from app.models.tenant_membership import TenantMembership
from app.services.user_service import register_user


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


@pytest.fixture
def authenticated_user(db_session):
    user = register_user(
        session=db_session,
        email="tenant-creator@example.com",
        password="StrongPassword123!",
    )
    db_session.flush()

    return user, create_access_token(user.id)


def create_tenant_with_member(
    db_session,
    user,
    tenant_name,
    role="admin",
):
    tenant = Tenant(name=tenant_name)
    db_session.add(tenant)
    db_session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role=role,
    )
    db_session.add(membership)
    db_session.flush()

    return tenant


# -------------------------
# Tenant creation tests
# -------------------------

def test_create_tenant_success_and_creator_becomes_admin(
    client,
    db_session,
    authenticated_user,
):
    user, token = authenticated_user

    response = client.post(
        "/tenants",
        json={"name": "Acme Knowledge Base"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201

    body = response.json()
    assert body["name"] == "Acme Knowledge Base"
    assert body["id"]

    membership = db_session.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == body["id"],
        )
    )

    assert membership is not None
    assert membership.role == "admin"


def test_create_tenant_without_token_returns_401(client):
    response = client.post(
        "/tenants",
        json={"name": "Unauthorized Tenant"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_duplicate_tenant_name_returns_409(
    client,
    authenticated_user,
):
    _, token = authenticated_user
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"name": "Duplicate Tenant"}

    first_response = client.post(
        "/tenants",
        json=payload,
        headers=headers,
    )
    second_response = client.post(
        "/tenants",
        json=payload,
        headers=headers,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


# -------------------------
# Tenant listing tests
# -------------------------

def test_list_tenants_returns_only_user_memberships(
    client,
    db_session,
    authenticated_user,
):
    user, token = authenticated_user

    tenant_a = create_tenant_with_member(
        db_session,
        user,
        "Tenant A",
    )
    tenant_b = create_tenant_with_member(
        db_session,
        user,
        "Tenant B",
        role="member",
    )

    # This tenant exists but the authenticated user is not a member.
    other_user = register_user(
        session=db_session,
        email="another-user@example.com",
        password="StrongPassword123!",
    )
    db_session.flush()

    create_tenant_with_member(
        db_session,
        other_user,
        "Private Tenant C",
    )

    response = client.get(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    returned_names = {tenant["name"] for tenant in response.json()}
    returned_ids = {tenant["id"] for tenant in response.json()}

    assert returned_names == {"Tenant A", "Tenant B"}
    assert "Private Tenant C" not in returned_names
    assert str(tenant_a.id) in returned_ids
    assert str(tenant_b.id) in returned_ids


def test_list_tenants_returns_empty_list_for_user_without_memberships(
    client,
    db_session,
):
    user = register_user(
        session=db_session,
        email="no-tenants@example.com",
        password="StrongPassword123!",
    )
    db_session.flush()

    token = create_access_token(user.id)

    response = client.get(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_list_tenants_without_token_returns_401(client):
    response = client.get("/tenants")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
