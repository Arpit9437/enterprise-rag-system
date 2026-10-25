from app.models.users import User
from app.core.security import hash_password, verify_password
from app.services.user_service import register_user

from sqlalchemy import select

import uuid
import pytest

def test_user_registration(db_session):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    password = "this is a test pass."

    user = register_user(session=db_session, email=email, password=password)

    statement = select(User).where(User.email == email)
    retrieved_user = db_session.scalar(statement)

    assert retrieved_user is not None
    assert retrieved_user.email == email
    assert retrieved_user.is_active is True
    assert retrieved_user.id is not None
    assert retrieved_user.created_at is not None
    assert user.password_hash != password
    assert verify_password(password, user.password_hash) is True


def test_registering_same_email_twice_raises_value_error(db_session):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    password = "SecurePassword123!"

    register_user(db_session, email, password)

    with pytest.raises(ValueError):
        register_user(db_session, email, password)