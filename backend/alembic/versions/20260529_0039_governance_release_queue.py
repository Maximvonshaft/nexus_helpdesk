"""governance release queue

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
    tables = _tables(bind)
    if "governance_release_requests" not in tables:
        op.create_table(
            "governance_release_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("release_type", sa.String(length=40), nullable=False, server_default="change"),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="pending_review"),
            sa.Column("risk_level", sa.String(length=40), nullable=False, server_default="medium"),
            sa.Column("impact_json", sa.JSON(), nullable=True),
            sa.Column("diff_json", sa.JSON(), nullable=True),
            sa.Column("rollback_plan", sa.Text(), nullable=True),
            sa.Column("audit_target_type", sa.String(length=80), nullable=True),
            sa.Column("audit_target_id", sa.Integer(), nullable=True),
            sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("rolled_back_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = _indexes(bind, "governance_release_requests")
    for name, columns in {
        "ix_governance_release_requests_source_type": ["source_type"],
        "ix_governance_release_requests_source_id": ["source_id"],
        "ix_governance_release_requests_title": ["title"],
        "ix_governance_release_requests_release_type": ["release_type"],
        "ix_governance_release_requests_status": ["status"],
        "ix_governance_release_requests_risk_level": ["risk_level"],
        "ix_governance_release_requests_audit_target_type": ["audit_target_type"],
        "ix_governance_release_requests_audit_target_id": ["audit_target_id"],
        "ix_governance_release_requests_requested_by": ["requested_by"],
        "ix_governance_release_requests_approved_by": ["approved_by"],
        "ix_governance_release_requests_published_by": ["published_by"],
        "ix_governance_release_requests_rolled_back_by": ["rolled_back_by"],
        "ix_governance_release_requests_created_at": ["created_at"],
        "ix_governance_release_requests_updated_at": ["updated_at"],
        "ix_governance_release_requests_submitted_at": ["submitted_at"],
        "ix_governance_release_requests_approved_at": ["approved_at"],
        "ix_governance_release_requests_published_at": ["published_at"],
        "ix_governance_release_requests_rolled_back_at": ["rolled_back_at"],
        "ix_governance_release_status_created": ["status", "created_at"],
        "ix_governance_release_source": ["source_type", "source_id"],
        "ix_governance_release_risk_status": ["risk_level", "status"],
    }.items():
        if name not in indexes:
            op.create_index(name, "governance_release_requests", columns)

    tables = _tables(bind)
    if "governance_release_events" not in tables:
        op.create_table(
            "governance_release_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("release_id", sa.Integer(), sa.ForeignKey("governance_release_requests.id"), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("request_id", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    event_indexes = _indexes(bind, "governance_release_events")
    for name, columns in {
        "ix_governance_release_events_release_id": ["release_id"],
        "ix_governance_release_events_actor_id": ["actor_id"],
        "ix_governance_release_events_event_type": ["event_type"],
        "ix_governance_release_events_request_id": ["request_id"],
        "ix_governance_release_events_created_at": ["created_at"],
        "ix_governance_release_events_release_created": ["release_id", "created_at"],
    }.items():
        if name not in event_indexes:
            op.create_index(name, "governance_release_events", columns)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "governance_release_events" in tables:
        op.drop_table("governance_release_events")
    if "governance_release_requests" in tables:
        op.drop_table("governance_release_requests")
