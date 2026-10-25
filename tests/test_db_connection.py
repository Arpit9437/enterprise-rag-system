from sqlalchemy import text
from app.db.engine import engine

def test_database_connection():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        value = result.scalar_one()
        assert value == 1
        print(f"Database connection successful: {value}")

if __name__ == "__main__":
    test_database_connection()