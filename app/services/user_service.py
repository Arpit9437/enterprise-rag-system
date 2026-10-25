
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.users import User
from app.core.security import hash_password, verify_password


def register_user(
    session: Session,
    email: str,
    password: str,
) -> User:
    existing_user = session.scalar(
        select(User).where(User.email == email)
    )

    if existing_user is not None:
        raise ValueError("A user with this email already exists.")

    hashed_password = hash_password(password)

    new_user = User(
        email=email,
        password_hash=hashed_password,
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
            User.email == email
        )
    )

    if user is None:
        return None

    if not verify_password(password=password, hashed_password=user.password_hash):
        return None

    if not user.is_active:
        return None

    return user
