from fastapi import APIRouter, Depends, status, HTTPException
from typing import Annotated
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenant_dependencies import require_tenant_admin
from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.tenants import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.users import User
from app.schemas.tenant import TenantCreate, TenantResponse, TenantMemberAdd, TenantMemberListResponse, TenantMemberResponse, TenantMemberRoleUpdate

router = APIRouter(prefix="/tenants", tags=["Tenants"])

@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant(
    payload: TenantCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    tenant = Tenant(name=payload.name.strip())

    if not tenant.name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tenant name cannot be blank",
        )

    try:
        db.add(tenant)
        db.flush()

        membership = TenantMembership(
            user_id=current_user.id,
            tenant_id=tenant.id,
            role="admin",
        )
        db.add(membership)

        db.commit()
        db.refresh(tenant)

        return tenant

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with this name already exists.",
        ) from exc

@router.get("", response_model=list[TenantResponse])
def list_tenants(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    statement = (
        select(Tenant)
        .join(
            TenantMembership,
            TenantMembership.tenant_id == Tenant.id,
        )
        .where(TenantMembership.user_id == current_user.id)
        .order_by(Tenant.created_at.desc())
    )

    return list(db.scalars(statement).all())


@router.post(
    "/{tenant_id}/members",
    response_model=TenantMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_tenant_member(
    tenant_id: UUID,
    payload: TenantMemberAdd,
    admin: Annotated[User, Depends(require_tenant_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    email = str(payload.email).lower()

    user = db.scalar(
        select(User).where(func.lower(User.email) == email)
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    existing = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this tenant",
        )

    membership = TenantMembership(
        tenant_id=tenant_id,
        user_id=user.id,
        role=payload.role,
    )

    try:
        db.add(membership)
        db.flush()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not add tenant membership",
        ) from exc

    return TenantMemberResponse(
        user_id=user.id,
        email=user.email,
        role=membership.role,
    )


@router.get(
    "/{tenant_id}/members",
    response_model=TenantMemberListResponse,
)
def list_tenant_members(
    tenant_id: UUID,
    admin: Annotated[User, Depends(require_tenant_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    statement = (
        select(TenantMembership, User.email)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(User.email)
    )

    rows = db.execute(statement).all()

    members = [
        TenantMemberResponse(
            user_id=membership.user_id,
            email=email,
            role=membership.role,
        )
        for membership, email in rows
    ]

    return TenantMemberListResponse(members=members)


@router.patch(
    "/{tenant_id}/members/{user_id}",
    response_model=TenantMemberResponse,
)
def update_tenant_member_role(
    tenant_id: UUID,
    user_id: UUID,
    payload: TenantMemberRoleUpdate,
    admin: Annotated[User, Depends(require_tenant_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
    )

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant membership not found",
        )

    if membership.role == "admin" and payload.role != "admin":
        admin_count = db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == "admin",
            )
        )

        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last tenant administrator",
            )

    membership.role = payload.role

    try:
        db.commit()
        db.refresh(membership)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not update tenant membership",
        ) from exc

    user = db.get(User, membership.user_id)

    return TenantMemberResponse(
        user_id=membership.user_id,
        email=user.email,
        role=membership.role,
    )


@router.delete(
    "/{tenant_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_tenant_member(
    tenant_id: UUID,
    user_id: UUID,
    admin: Annotated[User, Depends(require_tenant_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
    )

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant membership not found",
        )

    if membership.role == "admin":
        admin_count = db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == "admin",
            )
        )

        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the last tenant administrator",
            )

    try:
        db.delete(membership)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not remove tenant membership",
        ) from exc

    return None
