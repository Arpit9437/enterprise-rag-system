import uuid
from uuid import UUID
from datetime import datetime

from app.db.base import Base

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy import String, func, DateTime, Boolean, true

class User(Base):
    __tablename__="users"

    id : Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    email : Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    password_hash : Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active : Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    created_at : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )