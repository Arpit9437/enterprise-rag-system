import pytest

from app.core.config import settings
from app.db.engine import engine
from app.db.session import LocalSession


@pytest.fixture(autouse=True)
def use_test_jwt_secret(monkeypatch):
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        "test-only-jwt-secret-key-that-is-at-least-64-bytes-long-for-hs256",
    )
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "metrics_enabled", False)


@pytest.fixture
def db_session():
    connection = engine.connect()
    outer_transaction = connection.begin()

    session = LocalSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
