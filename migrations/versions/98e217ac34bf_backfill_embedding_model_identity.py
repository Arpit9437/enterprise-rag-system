"""backfill the embedding model for existing vectors

Revision ID: 98e217ac34bf
Revises: 3f8c13de5b67
Create Date: 2026-10-27 18:02:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = "98e217ac34bf"
down_revision: Union[str, Sequence[str], None] = "3f8c13de5b67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE document_chunks "
        "SET embedding_model = "
        "'sentence-transformers/all-MiniLM-L6-v2' "
        "WHERE embedding IS NOT NULL AND embedding_model IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE document_chunks SET embedding_model = NULL "
        "WHERE embedding_model = "
        "'sentence-transformers/all-MiniLM-L6-v2'"
    )
