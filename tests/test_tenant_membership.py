import uuid
import pytest

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.models.tenants import Tenant
from app.models.users import User
from app.models.tenant_membership import TenantMembership


def test_create_tenant_membership(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
    )

    db_session.add_all([tenant, user])
    db_session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="admin",
    )

    db_session.add(membership)
    db_session.flush()

    statement = select(TenantMembership).where(
        TenantMembership.user_id == user.id,
        TenantMembership.tenant_id == tenant.id,
    )
    retrieved = db_session.scalar(statement)

    assert retrieved is not None
    assert retrieved.role == "admin"
    assert retrieved.user_id == user.id
    assert retrieved.tenant_id == tenant.id
    assert retrieved.created_at is not None

def test_duplicate_membership_is_rejected(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
    )

    db_session.add_all([tenant, user])
    db_session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="member",
    )
    db_session.add(membership)
    db_session.flush()

    duplicate = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="admin",
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()

def test_invalid_membership_role_is_rejected(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
    )

    db_session.add_all([tenant, user])
    db_session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="superuser",
    )
    db_session.add(membership)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()
