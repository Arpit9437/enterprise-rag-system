
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
)


def test_create_and_decode_access_token():
    user_id = uuid.uuid4()

    token = create_access_token(user_id)
    decoded_user_id = decode_access_token(token)

    assert isinstance(token, str)
    assert decoded_user_id == user_id


def test_access_token_contains_subject_and_expiration():
    user_id = uuid.uuid4()

    token = create_access_token(user_id)

    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    assert payload["sub"] == str(user_id)
    assert "exp" in payload


def test_expired_access_token_is_rejected():
    user_id = uuid.uuid4()

    expired_token = jwt.encode(
        {
            "sub": str(user_id),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(expired_token) is None


def test_invalid_access_token_is_rejected():
    assert decode_access_token("not-a-valid-jwt") is None


def test_token_signed_with_wrong_secret_is_rejected():
    user_id = uuid.uuid4()

    token = jwt.encode(
        {
            "sub": str(user_id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "a-different-test-secret-that-is-long-enough-for-hs256-tests",
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_token_with_invalid_user_id_is_rejected():
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_token_without_required_expiration_claim_is_rejected():
    token = jwt.encode(
        {"sub": str(uuid.uuid4())},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_token_without_required_subject_claim_is_rejected():
    token = jwt.encode(
        {
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None
