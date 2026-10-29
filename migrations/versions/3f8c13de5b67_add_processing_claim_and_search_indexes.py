"""add processing leases and retrieval indexes

Revision ID: 3f8c13de5b67
Revises: eee019f1efa3
Create Date: 2026-10-27 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "3f8c13de5b67"
down_revision: Union[str, Sequence[str], None] = "eee019f1efa3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("processing_claimed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "documents",
        sa.Column("processing_claim_id", sa.UUID()),
    )
    op.add_column(
        "documents",
        sa.Column("chunk_size_used", sa.Integer()),
    )
    op.add_column(
        "documents",
        sa.Column("chunk_overlap_used", sa.Integer()),
    )
    op.add_column(
        "document_chunks",
        sa.Column("embedding_model", sa.String(length=255)),
    )
    op.create_index(
        "ix_documents_status_processing_claimed_at",
        "documents",
        ["status", "processing_claimed_at"],
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_content_english_gin "
        "ON document_chunks USING gin (to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_document_chunks_content_english_gin")
    op.execute("DROP INDEX ix_document_chunks_embedding_hnsw")
    op.drop_index(
        "ix_documents_status_processing_claimed_at",
        table_name="documents",
    )
    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("documents", "chunk_overlap_used")
    op.drop_column("documents", "chunk_size_used")
    op.drop_column("documents", "processing_claim_id")
    op.drop_column("documents", "processing_claimed_at")
