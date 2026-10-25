from sqlalchemy import text
from app.db.session import LocalSession

def test_db_session():
    session = LocalSession()
    try:
        result = session.execute(text("SELECT 1"))
        value = result.scalar_one()
        assert value == 1
        print(f"Database session : {value}")
    finally:
        session.close()

if __name__ == "__main__":
    test_db_session()