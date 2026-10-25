
import uuid

from sqlalchemy import select

from app.models.tenants import Tenant


def test_commit_is_rolled_back_after_test(db_session):
    tenant_name = f"commit-check-{uuid.uuid4().hex[:8]}"

    tenant = Tenant(name=tenant_name)
    db_session.add(tenant)

    # Explicitly commit through the SQLAlchemy session.
    db_session.commit()

    # The committed record must remain visible inside this test.
    statement = select(Tenant).where(Tenant.name == tenant_name)
    retrieved_tenant = db_session.scalar(statement)

    assert retrieved_tenant is not None
    assert retrieved_tenant.name == tenant_name

    print(f"Transaction test tenant: {tenant_name}")
