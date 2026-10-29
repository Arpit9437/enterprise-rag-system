from sqlalchemy import create_engine

from app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    hide_parameters=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
)