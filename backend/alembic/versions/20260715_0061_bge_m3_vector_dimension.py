"""align knowledge vectors with the native BGE-M3 dimension

Revision ID: 20260715_0061
Revises: 20260715_0060
Create Date: 2026-07-15

Embeddings are derived data. Existing vectors are invalidated before changing
the pgvector column type so they can be rebuilt by the configured provider.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0061"
down_revision = "20260715_0060"
branch_labels = None
depends_on = None

_TABLE = "knowledge_chunks"
_VECTOR_INDEX = "ix_knowledge_chunks_embedding_vector_ivfflat"


def _reset_embedding_state() -> None:
    op.execute(
        sa.text(
            """
            UPDATE knowledge_chunks
            SET embedding_vector = NULL,
                embedding = NULL,
                embedding_model = NULL,
                embedding_dim = NULL,
                embedding_status = 'pending',
                embedding_error = NULL,
                embedded_at = NULL,
                semantic_hash = NULL
            """
        )
    )


def _replace_postgres_vector_dimension(dimension: int) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_VECTOR_INDEX}"))
    op.execute(
        sa.text(
            f"ALTER TABLE {_TABLE} "
            f"ALTER COLUMN embedding_vector TYPE vector({dimension}) "
            f"USING NULL::vector({dimension})"
        )
    )
    op.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS {_VECTOR_INDEX} "
            f"ON {_TABLE} USING ivfflat "
            "(embedding_vector vector_cosine_ops) WITH (lists = 100)"
        )
    )


def upgrade() -> None:
    _reset_embedding_state()
    if op.get_bind().dialect.name == "postgresql":
        _replace_postgres_vector_dimension(1024)


def downgrade() -> None:
    _reset_embedding_state()
    if op.get_bind().dialect.name == "postgresql":
        _replace_postgres_vector_dimension(384)
