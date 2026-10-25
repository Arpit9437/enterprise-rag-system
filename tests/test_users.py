
import uuid
import pytest

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.users import User


def test_create_and_retrieve_user(db_session):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"

    user = User(
        email=email,
        password_hash="test-hash-not-a-real-password-hash",
    )

    db_session.add(user)
    db_session.flush()

    statement = select(User).where(User.email == email)
    retrieved_user = db_session.scalar(statement)

    assert retrieved_user is not None
    assert retrieved_user.email == email
    assert retrieved_user.is_active is True
    assert retrieved_user.id is not None
    assert retrieved_user.created_at is not None


def test_duplicate_user_email_is_rejected(db_session):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"

    first_user = User(
        email=email,
        password_hash="test-hash-1",
    )
    db_session.add(first_user)
    db_session.flush()

    duplicate_user = User(
        email=email,
        password_hash="test-hash-2",
    )
    db_session.add(duplicate_user)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()
