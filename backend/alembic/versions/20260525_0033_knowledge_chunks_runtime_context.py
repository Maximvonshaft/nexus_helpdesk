"""knowledge chunks and runtime context metadata

Revision ID: 20260525_0033
Revises: 20260523_0032
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260525_0033"
down_revision = "20260523_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("parsing_status", sa.String(length=40), nullable=False, server_default="unparsed"))
    op.add_column("knowledge_items", sa.Column("parsing_error", sa.Text(), nullable=True))
    op.add_column("knowledge_items", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_items", sa.Column("indexed_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("knowledge_items", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_items", sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_knowledge_items_parsing_status", "knowledge_items", ["parsing_status"], unique=False)
    op.create_index("ix_knowledge_items_parsed_at", "knowledge_items", ["parsed_at"], unique=False)
    op.create_index("ix_knowledge_items_indexed_version", "knowledge_items", ["indexed_version"], unique=False)
    op.create_index("ix_knowledge_items_indexed_at", "knowledge_items", ["indexed_at"], unique=False)

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("knowledge_items.id"), nullable=False),
        sa.Column("item_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("audience_scope", sa.String(length=40), nullable=False, server_default="customer"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("item_id", "published_version", "chunk_index", name="uq_knowledge_chunk_version_index"),
    )
    for name, columns in [
        ("ix_knowledge_chunks_item_id", ["item_id"]),
        ("ix_knowledge_chunks_item_key", ["item_key"]),
        ("ix_knowledge_chunks_published_version", ["published_version"]),
        ("ix_knowledge_chunks_chunk_index", ["chunk_index"]),
        ("ix_knowledge_chunks_content_hash", ["content_hash"]),
        ("ix_knowledge_chunks_market_id", ["market_id"]),
        ("ix_knowledge_chunks_channel", ["channel"]),
        ("ix_knowledge_chunks_audience_scope", ["audience_scope"]),
        ("ix_knowledge_chunks_status", ["status"]),
        ("ix_knowledge_chunks_priority", ["priority"]),
        ("ix_knowledge_chunks_created_at", ["created_at"]),
    ]:
        op.create_index(name, "knowledge_chunks", columns, unique=False)


def downgrade() -> None:
    for name in [
        "ix_knowledge_chunks_created_at",
        "ix_knowledge_chunks_priority",
        "ix_knowledge_chunks_status",
        "ix_knowledge_chunks_audience_scope",
        "ix_knowledge_chunks_channel",
        "ix_knowledge_chunks_market_id",
        "ix_knowledge_chunks_content_hash",
        "ix_knowledge_chunks_chunk_index",
        "ix_knowledge_chunks_published_version",
        "ix_knowledge_chunks_item_key",
        "ix_knowledge_chunks_item_id",
    ]:
        op.drop_index(name, table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index("ix_knowledge_items_indexed_at", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_indexed_version", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_parsed_at", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_parsing_status", table_name="knowledge_items")
    op.drop_column("knowledge_items", "chunk_count")
    op.drop_column("knowledge_items", "indexed_at")
    op.drop_column("knowledge_items", "indexed_version")
    op.drop_column("knowledge_items", "parsed_at")
    op.drop_column("knowledge_items", "parsing_error")
    op.drop_column("knowledge_items", "parsing_status")
