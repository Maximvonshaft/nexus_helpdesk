"""knowledge runtime v2 metadata

Revision ID: 20260601_0046
Revises: 20260530_0045
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260601_0046"
down_revision = "20260530_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("search_vector", sa.Text(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding", sa.JSON(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_model", sa.String(length=120), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_dim", sa.Integer(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_status", sa.String(length=40), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_error", sa.Text(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("retrieval_metadata_json", sa.JSON(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("section_path", sa.String(length=500), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("chunk_type", sa.String(length=80), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("source_document_id", sa.String(length=120), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("source_page", sa.Integer(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("source_row", sa.Integer(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("semantic_hash", sa.String(length=64), nullable=True))
    op.execute(sa.text("UPDATE knowledge_chunks SET embedding_status = 'pending' WHERE embedding_status IS NULL"))
    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.alter_column("embedding_status", nullable=False)
    op.create_index("ix_knowledge_chunks_embedding_model", "knowledge_chunks", ["embedding_model"], unique=False)
    op.create_index("ix_knowledge_chunks_embedding_status", "knowledge_chunks", ["embedding_status"], unique=False)
    op.create_index("ix_knowledge_chunks_embedded_at", "knowledge_chunks", ["embedded_at"], unique=False)
    op.create_index("ix_knowledge_chunks_chunk_type", "knowledge_chunks", ["chunk_type"], unique=False)
    op.create_index("ix_knowledge_chunks_source_document_id", "knowledge_chunks", ["source_document_id"], unique=False)
    op.create_index("ix_knowledge_chunks_semantic_hash", "knowledge_chunks", ["semantic_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_semantic_hash", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_source_document_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_chunk_type", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_embedded_at", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_embedding_status", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_embedding_model", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "semantic_hash")
    op.drop_column("knowledge_chunks", "source_row")
    op.drop_column("knowledge_chunks", "source_page")
    op.drop_column("knowledge_chunks", "source_document_id")
    op.drop_column("knowledge_chunks", "chunk_type")
    op.drop_column("knowledge_chunks", "section_path")
    op.drop_column("knowledge_chunks", "retrieval_metadata_json")
    op.drop_column("knowledge_chunks", "embedded_at")
    op.drop_column("knowledge_chunks", "embedding_error")
    op.drop_column("knowledge_chunks", "embedding_status")
    op.drop_column("knowledge_chunks", "embedding_dim")
    op.drop_column("knowledge_chunks", "embedding_model")
    op.drop_column("knowledge_chunks", "embedding")
    op.drop_column("knowledge_chunks", "search_vector")
