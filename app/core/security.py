
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.core.config import settings


password_hash = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = password_hash.hash(
    "password-verification-timing-only"
)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(
    password: str,
    hashed_password: str,
) -> bool:
    try:
        return password_hash.verify(password, hashed_password)
    except PwdlibError:
        return False


def verify_login_password(
    password: str,
    hashed_password: str | None,
) -> bool:
    if hashed_password is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return False

    try:
        return password_hash.verify(password, hashed_password)
    except PwdlibError:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return False


def create_access_token(user_id: UUID) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )

    payload = {
        "sub": str(user_id),
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> UUID | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )

        subject = payload.get("sub")

        if not isinstance(subject, str):
            return None

        return UUID(subject)

    except (InvalidTokenError, ValueError, TypeError):
        return None
