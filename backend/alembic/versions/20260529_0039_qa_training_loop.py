"""qa training loop

Revision ID: 20260529_0039
Revises: 20260527_0038
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260529_0039"
down_revision = "20260527_0038"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = _tables(bind)
    if "qa_reviews" not in existing_tables:
        op.create_table(
            "qa_reviews",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("sample_channel", sa.String(length=60), nullable=False),
            sa.Column("sample_ref", sa.String(length=160), nullable=True),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("agent_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="reviewed"),
            sa.Column("ai_pre_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("final_score", sa.Integer(), nullable=True),
            sa.Column("risks_json", sa.Text(), nullable=True),
            sa.Column("feedback", sa.Text(), nullable=True),
            sa.Column("knowledge_gap_summary", sa.Text(), nullable=True),
            sa.Column("appeal_status", sa.String(length=40), nullable=False, server_default="not_started"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    review_indexes = _indexes(bind, "qa_reviews")
    for index_name, columns in {
        "ix_qa_reviews_ticket_id": ["ticket_id"],
        "ix_qa_reviews_sample_channel": ["sample_channel"],
        "ix_qa_reviews_sample_ref": ["sample_ref"],
        "ix_qa_reviews_reviewer_id": ["reviewer_id"],
        "ix_qa_reviews_agent_id": ["agent_id"],
        "ix_qa_reviews_status": ["status"],
        "ix_qa_reviews_appeal_status": ["appeal_status"],
        "ix_qa_reviews_created_at": ["created_at"],
        "ix_qa_reviews_ticket_created": ["ticket_id", "created_at"],
        "ix_qa_reviews_status_created": ["status", "created_at"],
        "ix_qa_reviews_channel_status": ["sample_channel", "status"],
        "ix_qa_reviews_agent_status": ["agent_id", "status"],
    }.items():
        if index_name not in review_indexes:
            op.create_index(index_name, "qa_reviews", columns)

    if "qa_training_tasks" not in _tables(bind):
        op.create_table(
            "qa_training_tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("review_id", sa.Integer(), sa.ForeignKey("qa_reviews.id"), nullable=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("agent_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("task_type", sa.String(length=60), nullable=False, server_default="coaching"),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("knowledge_gap_summary", sa.Text(), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    task_indexes = _indexes(bind, "qa_training_tasks")
    for index_name, columns in {
        "ix_qa_training_tasks_review_id": ["review_id"],
        "ix_qa_training_tasks_ticket_id": ["ticket_id"],
        "ix_qa_training_tasks_agent_id": ["agent_id"],
        "ix_qa_training_tasks_owner_id": ["owner_id"],
        "ix_qa_training_tasks_task_type": ["task_type"],
        "ix_qa_training_tasks_status": ["status"],
        "ix_qa_training_tasks_due_at": ["due_at"],
        "ix_qa_training_tasks_created_by": ["created_by"],
        "ix_qa_training_tasks_created_at": ["created_at"],
        "ix_qa_training_tasks_status_due": ["status", "due_at"],
        "ix_qa_training_tasks_agent_status": ["agent_id", "status"],
        "ix_qa_training_tasks_ticket_status": ["ticket_id", "status"],
    }.items():
        if index_name not in task_indexes:
            op.create_index(index_name, "qa_training_tasks", columns)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "qa_training_tasks" in tables:
        op.drop_table("qa_training_tasks")
    if "qa_reviews" in tables:
        op.drop_table("qa_reviews")
