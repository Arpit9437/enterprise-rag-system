"""add stable ordering for conversation messages

Revision ID: 91d5b02ce7a4
Revises: 4f2c8a9d1e73
Create Date: 2026-10-27 21:15:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91d5b02ce7a4"
down_revision: Union[str, Sequence[str], None] = "4f2c8a9d1e73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column(
            "message_index",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.execute(
        """
        WITH ordered_messages AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY conversation_id
                       ORDER BY created_at, id
                   ) - 1 AS message_index
            FROM conversation_messages
        )
        UPDATE conversation_messages AS messages
        SET message_index = ordered_messages.message_index
        FROM ordered_messages
        WHERE messages.id = ordered_messages.id
        """
    )
    op.drop_index(
        "ix_conversation_messages_conversation_created",
        table_name="conversation_messages",
    )
    op.create_unique_constraint(
        "uq_conversation_messages_conversation_index",
        "conversation_messages",
        ["conversation_id", "message_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_conversation_messages_conversation_index",
        "conversation_messages",
        type_="unique",
    )
    op.drop_column("conversation_messages", "message_index")
    op.create_index(
        "ix_conversation_messages_conversation_created",
        "conversation_messages",
        ["conversation_id", "created_at", "id"],
    )
