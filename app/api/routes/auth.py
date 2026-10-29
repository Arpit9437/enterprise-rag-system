from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.tenant_membership import TenantMembership
from app.db.session import get_db
from app.services.user_service import register_user, authenticate_user
from app.schemas.user import UserRegister, UserResponse, TokenResponse, UserLogin
from app.core.security import create_access_token
from app.core.dependencies import get_current_user
from app.core.rate_limit import rate_limit
from app.models.users import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an account",
    description=(
        "Create an account using a normalized unique email address. "
        "Passwords are stored as hashes; registration does not sign in the "
        "new account."
    ),
    responses={
        409: {"description": "An account with this email already exists."},
    },
    dependencies=[
        Depends(rate_limit("register", "rate_limit_login_requests"))
    ],
)
def register(
    payload: UserRegister,
    db: Session = Depends(get_db),
):
    """Create an account; duplicate email addresses return 409."""
    try:
        user = register_user(
            session=db,
            email=str(payload.email),
            password=payload.password,
        )
        db.commit()
        db.refresh(user)
        return user

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        ) from exc

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in",
    description=(
        "Authenticate with email and password. Invalid credentials return "
        "the same 401 response without revealing whether the email exists. "
        "The bearer token expires according to ACCESS_TOKEN_EXPIRE_MINUTES."
    ),
    responses={
        401: {"description": "Invalid email or password."},
        429: {"description": "Login rate limit exceeded."},
    },
    dependencies=[
        Depends(rate_limit("login", "rate_limit_login_requests"))
    ],
)
def login(
    payload: UserLogin,
    db: Session = Depends(get_db),
):
    """Exchange valid credentials for a time-limited bearer access token."""
    user = authenticate_user(
        email=str(payload.email),
        password=payload.password,
        session=db
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user_id=user.id)

    return TokenResponse(access_token=access_token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current account",
    description="Requires a valid, unexpired bearer access token.",
    responses={401: {"description": "Missing, invalid, or expired token."}},
)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
):
    return current_user


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the current account",
    description=(
        "Deletes the authenticated account and memberships. An account "
        "cannot be deleted while it is the last administrator of a tenant."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        409: {"description": "The account is the last administrator of a tenant."},
    },
)
def delete_me(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> None:
    memberships = db.scalars(
        select(TenantMembership).where(
            TenantMembership.user_id == current_user.id
        )
    ).all()
    for membership in memberships:
        if membership.role != "admin":
            continue
        admin_count = db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.tenant_id == membership.tenant_id,
                TenantMembership.role == "admin",
            )
        ) or 0
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Transfer tenant administrator access or delete the "
                    "tenant before deleting this account."
                ),
            )

    db.delete(current_user)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete account",
        ) from None
