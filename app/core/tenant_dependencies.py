from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.users import User
from app.services.tenant_access import (
    has_tenant_access,
    has_tenant_role,
)

def require_tenant_member(
    tenant_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    has_access = has_tenant_access(
        session=db,
        user_id=current_user.id,
        tenant_id=tenant_id,
    )

    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found or access denied",
        )

    return current_user


def require_tenant_admin(
    tenant_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    has_access = has_tenant_access(
        session=db,
        user_id=current_user.id,
        tenant_id=tenant_id,
    )

    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found or access denied",
        )

    is_admin = has_tenant_role(
        session=db,
        user_id=current_user.id,
        tenant_id=tenant_id,
        required_role="admin",
    )

    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant administrator privileges required",
        )

    return current_user
