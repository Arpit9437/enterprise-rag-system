
from uuid import uuid4

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


def create_user(db_session, email):
    user = register_user(
        session=db_session,
        email=email,
        password="StrongPassword123!",
    )
    db_session.flush()
    return user


def create_tenant(db_session, admin_user):
    tenant = Tenant(name=f"Tenant-{uuid4().hex[:12]}")
    db_session.add(tenant)
    db_session.flush()

    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=admin_user.id,
        role="admin",
    )
    db_session.add(membership)
    db_session.flush()

    return tenant


def add_membership(db_session, tenant, user, role="member"):
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=role,
    )
    db_session.add(membership)
    db_session.flush()
    return membership


def auth_headers(user):
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def test_admin_can_add_existing_user(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    new_user = create_user(
        db_session,
        f"member-{uuid4().hex}@example.com",
    )

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
        json={"email": new_user.email, "role": "member"},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(new_user.id)
    assert response.json()["email"] == new_user.email
    assert response.json()["role"] == "member"

    membership = db_session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == new_user.id,
        )
    )
    assert membership is not None


def test_admin_can_assign_admin_role(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    new_user = create_user(
        db_session,
        f"new-admin-{uuid4().hex}@example.com",
    )

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
        json={"email": new_user.email, "role": "admin"},
    )

    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_regular_member_cannot_add_members(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    target = create_user(db_session, f"target-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member, role="member")

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(member),
        json={"email": target.email, "role": "member"},
    )

    assert response.status_code == 403


def test_cannot_add_unknown_user(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
        json={"email": "unknown@example.com", "role": "member"},
    )

    assert response.status_code == 404


def test_cannot_add_duplicate_membership(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member)

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
        json={"email": member.email, "role": "member"},
    )

    assert response.status_code == 409


def test_admin_can_list_members(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member)

    response = client.get(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
    )

    assert response.status_code == 200

    members = response.json()["members"]
    member_by_id = {item["user_id"]: item for item in members}

    assert str(admin.id) in member_by_id
    assert str(member.id) in member_by_id
    assert member_by_id[str(member.id)]["role"] == "member"


def test_regular_member_cannot_list_members(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member)

    response = client.get(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(member),
    )

    assert response.status_code == 403


def test_admin_can_promote_member(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member)

    response = client.patch(
        f"/tenants/{tenant.id}/members/{member.id}",
        headers=auth_headers(admin),
        json={"role": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_admin_can_remove_member(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member)

    response = client.delete(
        f"/tenants/{tenant.id}/members/{member.id}",
        headers=auth_headers(admin),
    )

    assert response.status_code == 204

    membership = db_session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == member.id,
        )
    )
    assert membership is None


def test_regular_member_cannot_remove_another_member(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    target = create_user(db_session, f"target-{uuid4().hex}@example.com")
    add_membership(db_session, tenant, member)
    add_membership(db_session, tenant, target)

    response = client.delete(
        f"/tenants/{tenant.id}/members/{target.id}",
        headers=auth_headers(member),
    )

    assert response.status_code == 403


def test_member_cannot_promote_themselves(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    membership = add_membership(db_session, tenant, member)

    response = client.patch(
        f"/tenants/{tenant.id}/members/{member.id}",
        headers=auth_headers(member),
        json={"role": "admin"},
    )

    db_session.refresh(membership)
    assert response.status_code == 403
    assert membership.role == "member"


def test_admin_cannot_assign_unsupported_role(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    target = create_user(db_session, f"target-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
        json={"email": target.email, "role": "owner"},
    )

    assert response.status_code == 422
    assert db_session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == target.id,
        )
    ) is None


def test_cannot_remove_last_admin(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)

    response = client.delete(
        f"/tenants/{tenant.id}/members/{admin.id}",
        headers=auth_headers(admin),
    )

    assert response.status_code == 409

    membership = db_session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == admin.id,
        )
    )
    assert membership is not None
    assert membership.role == "admin"


def test_cannot_demote_last_admin(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)

    response = client.patch(
        f"/tenants/{tenant.id}/members/{admin.id}",
        headers=auth_headers(admin),
        json={"role": "member"},
    )

    assert response.status_code == 409


def test_cannot_assign_invalid_role(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)
    member = create_user(db_session, f"member-{uuid4().hex}@example.com")

    response = client.post(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(admin),
        json={"email": member.email, "role": "owner"},
    )

    assert response.status_code == 422


def test_non_member_cannot_manage_another_tenant(
    client,
    db_session,
):
    admin = create_user(db_session, f"admin-{uuid4().hex}@example.com")
    tenant = create_tenant(db_session, admin)

    outsider = create_user(
        db_session,
        f"outsider-{uuid4().hex}@example.com",
    )

    response = client.get(
        f"/tenants/{tenant.id}/members",
        headers=auth_headers(outsider),
    )

    assert response.status_code == 404
