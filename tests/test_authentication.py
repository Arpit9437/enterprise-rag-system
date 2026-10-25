
import uuid

from app.core.security import hash_password
from app.models.users import User
from app.services.user_service import authenticate_user


def create_test_user(db_session, is_active=True):
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("SecurePassword123!"),
        is_active=is_active,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_authenticate_user_with_correct_password(db_session):
    user = create_test_user(db_session)

    authenticated_user = authenticate_user(
        session=db_session,
        email=user.email,
        password="SecurePassword123!",
    )

    assert authenticated_user is not None
    assert authenticated_user.id == user.id


def test_authenticate_user_with_wrong_password(db_session):
    user = create_test_user(db_session)

    authenticated_user = authenticate_user(
        session=db_session,
        email=user.email,
        password="WrongPassword123!",
    )

    assert authenticated_user is None


def test_authenticate_nonexistent_user(db_session):
    authenticated_user = authenticate_user(
        session=db_session,
        email="missing@example.com",
        password="SecurePassword123!",
    )

    assert authenticated_user is None


def test_authenticate_inactive_user(db_session):
    user = create_test_user(db_session, is_active=False)

    authenticated_user = authenticate_user(
        session=db_session,
        email=user.email,
        password="SecurePassword123!",
    )

    assert authenticated_user is None
