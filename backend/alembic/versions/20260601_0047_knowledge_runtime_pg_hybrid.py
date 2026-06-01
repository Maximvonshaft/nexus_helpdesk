"""knowledge runtime postgres hybrid indexes

Revision ID: 20260601_0047
Revises: 20260601_0046
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260601_0047"
down_revision = "20260601_0046"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        op.execute(sa.text("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS search_tsvector tsvector"))
        op.execute(sa.text("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_vector vector(1536)"))
    else:
        op.add_column("knowledge_chunks", sa.Column("search_tsvector", sa.Text(), nullable=True))
        op.add_column("knowledge_chunks", sa.Column("embedding_vector", sa.Text(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("lexical_config", sa.String(length=40), nullable=True))
    op.create_index("ix_knowledge_chunks_lexical_config", "knowledge_chunks", ["lexical_config"], unique=False)
    if _is_postgres():
        op.execute(sa.text("""
            UPDATE knowledge_chunks
            SET lexical_config = COALESCE(lexical_config, 'simple'),
                search_tsvector = to_tsvector('simple', COALESCE(search_vector, normalized_text, chunk_text, ''))
            WHERE search_tsvector IS NULL
        """))
        op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_search_tsvector_gin ON knowledge_chunks USING gin(search_tsvector)"))
        op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_vector_ivfflat ON knowledge_chunks USING ivfflat (embedding_vector vector_cosine_ops) WITH (lists = 100)"))


def downgrade() -> None:
    if _is_postgres():
        op.execute(sa.text("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_vector_ivfflat"))
        op.execute(sa.text("DROP INDEX IF EXISTS ix_knowledge_chunks_search_tsvector_gin"))
    op.drop_index("ix_knowledge_chunks_lexical_config", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "lexical_config")
    op.drop_column("knowledge_chunks", "embedding_vector")
    op.drop_column("knowledge_chunks", "search_tsvector")
