
import uuid

from app.models.tenants import Tenant
from app.models.users import User
from app.models.tenant_membership import TenantMembership
from app.services.tenant_access import has_tenant_access, has_tenant_role


def test_member_has_tenant_access(db_session):
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

    assert has_tenant_access(db_session, user.id, tenant.id) is True


def test_non_member_has_no_tenant_access(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
    )

    db_session.add_all([tenant, user])
    db_session.flush()

    # No membership is created.
    assert has_tenant_access(db_session, user.id, tenant.id) is False

def test_non_member_has_no_tenant_access(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
    )

    db_session.add_all([tenant, user])
    db_session.flush()

    # No membership is created.
    assert has_tenant_access(db_session, user.id, tenant.id) is False

def test_admin_has_admin_role(db_session):
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

    assert has_tenant_role(
        db_session, user.id, tenant.id, "admin"
    ) is True

def test_member_does_not_have_admin_role(db_session):
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

    assert has_tenant_role(
        db_session, user.id, tenant.id, "admin"
    ) is False


def test_non_member_does_not_have_admin_role(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
    )

    db_session.add_all([tenant, user])
    db_session.flush()

    # No TenantMembership is created.

    assert has_tenant_role(
        db_session, user.id, tenant.id, "admin"
    ) is False

