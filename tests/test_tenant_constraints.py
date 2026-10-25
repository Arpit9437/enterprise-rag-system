
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.tenants import Tenant


def test_duplicate_tenant_name_is_rejected(db_session):
    tenant_name = f"tenant-{uuid.uuid4().hex[:8]}"

    first_tenant = Tenant(name=tenant_name)
    db_session.add(first_tenant)
    db_session.commit()

    duplicate_tenant = Tenant(name=tenant_name)
    db_session.add(duplicate_tenant)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()

    # The original tenant must still exist.
    statement = select(Tenant).where(Tenant.name == tenant_name)
    existing_tenant = db_session.scalar(statement)

    assert existing_tenant is not None
    assert existing_tenant.name == tenant_name


def test_tenant_name_cannot_be_null(db_session):
    tenant = Tenant(name=None)
    db_session.add(tenant)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()
