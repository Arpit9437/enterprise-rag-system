import pytest
from fastapi.testclient import TestClient

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
)
from app.db.session import get_db
from app.main import app
from app.models.users import User
from app.services.user_service import register_user


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_user(
    db_session,
    email,
    password="CorrectPassword123!",
):
    user = register_user(
        session=db_session,
        email=email,
        password=password,
    )
    db_session.flush()
    return user


def create_inactive_user(db_session, email, password):
    user = User(
        email=email,
        password_hash=hash_password(password),
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()
    return user


# Registration tests

def test_register_user_success(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "StrongPassword123!",
        },
    )

    assert response.status_code == 201
    assert response.json()["email"] == "newuser@example.com"
    assert "password" not in response.json()
    assert "password_hash" not in response.json()


def test_register_duplicate_email_returns_409(client):
    payload = {
        "email": "duplicate@example.com",
        "password": "StrongPassword123!",
    }

    first = client.post("/auth/register", json=payload)
    second = client.post("/auth/register", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


def test_register_email_is_normalized_and_case_insensitive(
    client,
    db_session,
):
    first = client.post(
        "/auth/register",
        json={
            "email": "  MixedCase@example.com ",
            "password": "StrongPassword123!",
        },
    )
    second = client.post(
        "/auth/register",
        json={
            "email": "mixedcase@EXAMPLE.com",
            "password": "AnotherPassword123!",
        },
    )

    assert first.status_code == 201
    assert first.json()["email"] == "mixedcase@example.com"
    assert second.status_code == 409

    stored_user = db_session.query(User).filter_by(
        email="mixedcase@example.com"
    ).one()
    assert stored_user.password_hash != "StrongPassword123!"
    assert "StrongPassword123!" not in stored_user.password_hash


def test_register_invalid_email_returns_422(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "not-an-email",
            "password": "StrongPassword123!",
        },
    )

    assert response.status_code == 422


def test_register_short_password_returns_422(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "short@example.com",
            "password": "short",
        },
    )

    assert response.status_code == 422


# Login tests

def test_login_success_returns_access_token(client, db_session):
    user = create_user(db_session, "login@example.com")

    response = client.post(
        "/auth/login",
        json={
            "email": "login@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert decode_access_token(body["access_token"]) == user.id


def test_login_email_is_case_insensitive(client, db_session):
    create_user(db_session, "case-login@example.com")

    response = client.post(
        "/auth/login",
        json={
            "email": "  CASE-LOGIN@EXAMPLE.COM ",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_wrong_password_returns_401(client, db_session):
    create_user(db_session, "wrong-password@example.com")

    response = client.post(
        "/auth/login",
        json={
            "email": "wrong-password@example.com",
            "password": "IncorrectPassword123!",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_unknown_email_returns_401(client):
    response = client.post(
        "/auth/login",
        json={
            "email": "unknown@example.com",
            "password": "SomePassword123!",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_inactive_user_returns_401(client, db_session):
    create_inactive_user(
        db_session,
        "inactive-login@example.com",
        "CorrectPassword123!",
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "inactive-login@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 401


def test_login_invalid_email_returns_422(client):
    response = client.post(
        "/auth/login",
        json={
            "email": "not-an-email",
            "password": "SomePassword123!",
        },
    )

    assert response.status_code == 422


def test_login_missing_password_returns_422(client):
    response = client.post(
        "/auth/login",
        json={"email": "missing@example.com"},
    )

    assert response.status_code == 422


# Protected endpoint tests

def test_me_with_valid_token_returns_current_user(client, db_session):
    user = create_user(db_session, "me@example.com")
    token = create_access_token(user.id)

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"
    assert response.json()["is_active"] is True
    assert "password_hash" not in response.json()


def test_me_without_token_returns_401(client):
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_invalid_token_returns_401(client):
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid.token.value"},
    )

    assert response.status_code == 401


def test_me_with_inactive_user_token_returns_401(client, db_session):
    user = create_inactive_user(
        db_session,
        "me-inactive@example.com",
        "CorrectPassword123!",
    )
    token = create_access_token(user.id)

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_me_with_deleted_user_token_returns_401(client, db_session):
    user = create_user(db_session, "deleted-user@example.com")
    token = create_access_token(user.id)

    db_session.delete(user)
    db_session.flush()

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
