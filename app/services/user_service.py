from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_login_password
from app.models.users import User


def register_user(
    session: Session,
    email: str,
    password: str,
) -> User:
    email = email.strip().lower()
    if not email:
        raise ValueError("An email address is required.")

    existing_user = session.scalar(
        select(User).where(func.lower(User.email) == email)
    )

    if existing_user is not None:
        raise ValueError("A user with this email already exists.")

    new_user = User(
        email=email,
        password_hash=hash_password(password),
    )

    session.add(new_user)
    session.flush()

    return new_user


def authenticate_user(
    email: str,
    password: str,
    session: Session,
) -> User | None:
    user = session.scalar(
        select(User).where(
            func.lower(User.email) == email.strip().lower()
        )
    )

    if not verify_login_password(
        password,
        user.password_hash if user is not None else None,
    ):
        return None

    if user is None or not user.is_active:
        return None

    return user
