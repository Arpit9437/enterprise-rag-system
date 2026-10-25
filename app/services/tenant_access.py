
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant_membership import TenantMembership


def has_tenant_access(
    session: Session,
    user_id: UUID,
    tenant_id: UUID,
) -> bool:
    statement = select(TenantMembership.id).where(
        TenantMembership.user_id == user_id,
        TenantMembership.tenant_id == tenant_id,
    )

    membership_id = session.scalar(statement)

    return membership_id is not None

def has_tenant_role(
        session: Session, 
        user_id: UUID,
        tenant_id: UUID,
        required_role: str,
) -> bool:
    stmt = select(TenantMembership).where(
        TenantMembership.user_id == user_id,
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.role == required_role,
    )

    membership_id = session.scalar(stmt)
    
    return membership_id is not None