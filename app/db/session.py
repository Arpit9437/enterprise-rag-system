from sqlalchemy.orm import sessionmaker
from .engine import engine

LocalSession = sessionmaker(autoflush=False, autocommit=False, bind=engine)

def get_db():
    session = LocalSession()
    try:
        yield session
    finally:
        session.close()
