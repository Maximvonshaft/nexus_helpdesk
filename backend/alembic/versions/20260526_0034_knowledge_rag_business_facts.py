"""knowledge RAG business fact fields

Revision ID: 20260526_0034
Revises: 20260525_0033
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260526_0034"
down_revision = "20260525_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("knowledge_kind", sa.String(length=40), nullable=False, server_default="document"))
    op.add_column("knowledge_items", sa.Column("language", sa.String(length=16), nullable=True))
    op.add_column("knowledge_items", sa.Column("fact_question", sa.Text(), nullable=True))
    op.add_column("knowledge_items", sa.Column("fact_answer", sa.Text(), nullable=True))
    op.add_column("knowledge_items", sa.Column("fact_aliases_json", sa.JSON(), nullable=True))
    op.add_column("knowledge_items", sa.Column("fact_status", sa.String(length=40), nullable=False, server_default="draft"))
    op.add_column("knowledge_items", sa.Column("answer_mode", sa.String(length=40), nullable=False, server_default="guided_answer"))
    op.add_column("knowledge_items", sa.Column("citation_metadata_json", sa.JSON(), nullable=True))
    for name, columns in [
        ("ix_knowledge_items_knowledge_kind", ["knowledge_kind"]),
        ("ix_knowledge_items_language", ["language"]),
        ("ix_knowledge_items_fact_status", ["fact_status"]),
        ("ix_knowledge_items_answer_mode", ["answer_mode"]),
    ]:
        op.create_index(name, "knowledge_items", columns, unique=False)

    op.add_column("knowledge_chunks", sa.Column("language", sa.String(length=16), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("knowledge_kind", sa.String(length=40), nullable=False, server_default="document"))
    op.add_column("knowledge_chunks", sa.Column("fact_status", sa.String(length=40), nullable=False, server_default="draft"))
    op.add_column("knowledge_chunks", sa.Column("answer_mode", sa.String(length=40), nullable=False, server_default="guided_answer"))
    for name, columns in [
        ("ix_knowledge_chunks_language", ["language"]),
        ("ix_knowledge_chunks_knowledge_kind", ["knowledge_kind"]),
        ("ix_knowledge_chunks_fact_status", ["fact_status"]),
        ("ix_knowledge_chunks_answer_mode", ["answer_mode"]),
    ]:
        op.create_index(name, "knowledge_chunks", columns, unique=False)


def downgrade() -> None:
    for name in [
        "ix_knowledge_chunks_answer_mode",
        "ix_knowledge_chunks_fact_status",
        "ix_knowledge_chunks_knowledge_kind",
        "ix_knowledge_chunks_language",
    ]:
        op.drop_index(name, table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "answer_mode")
    op.drop_column("knowledge_chunks", "fact_status")
    op.drop_column("knowledge_chunks", "knowledge_kind")
    op.drop_column("knowledge_chunks", "language")

    for name in [
        "ix_knowledge_items_answer_mode",
        "ix_knowledge_items_fact_status",
        "ix_knowledge_items_language",
        "ix_knowledge_items_knowledge_kind",
    ]:
        op.drop_index(name, table_name="knowledge_items")
    op.drop_column("knowledge_items", "citation_metadata_json")
    op.drop_column("knowledge_items", "answer_mode")
    op.drop_column("knowledge_items", "fact_status")
    op.drop_column("knowledge_items", "fact_aliases_json")
    op.drop_column("knowledge_items", "fact_answer")
    op.drop_column("knowledge_items", "fact_question")
    op.drop_column("knowledge_items", "language")
    op.drop_column("knowledge_items", "knowledge_kind")
