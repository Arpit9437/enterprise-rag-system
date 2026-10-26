import pytest

from app.db.engine import engine
from app.db.session import LocalSession


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
