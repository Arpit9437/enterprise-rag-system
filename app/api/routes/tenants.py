import logging

from fastapi import APIRouter, Depends, status, HTTPException
from typing import Annotated
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.tenant_dependencies import require_tenant_admin
from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.tenants import Tenant
from app.models.document import Document
from app.models.tenant_membership import TenantMembership
from app.models.users import User
from app.schemas.tenant import TenantCreate, TenantResponse, TenantMemberAdd, TenantMemberListResponse, TenantMemberResponse, TenantMemberRoleUpdate
from app.services.document_storage import delete_stored_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tenants", tags=["Tenants"])

@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant",
    description=(
        "Create a workspace. The authenticated user becomes its first "
        "administrator."
    ),
)
def create_tenant(
    payload: TenantCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    tenant = Tenant(name=payload.name.strip())

    if not tenant.name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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

@router.get(
    "",
    response_model=list[TenantResponse],
    summary="List the current user's tenants",
    description=(
        "Returns only tenants where the current authenticated account has "
        "a membership."
    ),
)
def list_tenants(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """List only tenants where the authenticated user is a member."""
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


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tenant",
    description=(
        "Administrator-only. Deletes tenant records transactionally, then "
        "attempts local file cleanup."
    ),
)
def delete_tenant(
    tenant_id: UUID,
    admin: Annotated[User, Depends(require_tenant_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    storage_keys = db.scalars(
        select(Document.storage_key).where(
            Document.tenant_id == tenant_id,
            Document.storage_key.is_not(None),
        )
    ).all()
    try:
        result = db.execute(
            delete(Tenant).where(Tenant.id == tenant_id)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not delete tenant tenant_id=%s", tenant_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete tenant",
        ) from None

    for storage_key in storage_keys:
        try:
            delete_stored_file(storage_key)
        except (OSError, ValueError):
            logger.exception(
                "Tenant deletion left an orphaned file tenant_id=%s; "
                "run the orphan-file cleanup procedure",
                tenant_id,
            )


@router.post(
    "/{tenant_id}/members",
    response_model=TenantMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a tenant member",
    description=(
        "Administrator-only. Add an existing account by email with the "
        "member role by default; only tenant administrators can grant roles."
    ),
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
    summary="List tenant members",
    description="Administrator-only member and role listing.",
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
    summary="Update a member role",
    description=(
        "Administrator-only. Roles are restricted to admin or member; the "
        "last tenant administrator cannot be demoted."
    ),
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
    summary="Remove a tenant member",
    description=(
        "Administrator-only. The last tenant administrator cannot be removed."
    ),
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
