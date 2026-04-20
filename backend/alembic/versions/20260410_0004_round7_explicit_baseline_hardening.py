"""round7 explicit baseline hardening

Revision ID: 20260410_0004
Revises: 20260410_0003
Create Date: 2026-04-10 00:04:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0004"
down_revision = "20260410_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_indexes = {idx["name"] for table in inspector.get_table_names() for idx in inspector.get_indexes(table)}

    def create_index(name: str, table: str, cols: list[str], unique: bool = False):
        if name not in existing_indexes:
            op.create_index(name, table, cols, unique=unique)

    create_index("ix_tickets_status_updated_at", "tickets", ["status", "updated_at"])
    create_index("ix_tickets_team_status", "tickets", ["team_id", "status"])
    create_index("ix_ticket_outbound_messages_status_next_retry", "ticket_outbound_messages", ["status", "next_retry_at"])
    create_index("ix_background_jobs_job_type_status", "background_jobs", ["job_type", "status"])
    create_index("ix_integration_request_logs_client_created_at", "integration_request_logs", ["client_id", "created_at"])
    create_index("ix_user_capability_overrides_user_capability", "user_capability_overrides", ["user_id", "capability"], unique=True)


def downgrade() -> None:
    for name, table in [
        ("ix_user_capability_overrides_user_capability", "user_capability_overrides"),
        ("ix_integration_request_logs_client_created_at", "integration_request_logs"),
        ("ix_background_jobs_job_type_status", "background_jobs"),
        ("ix_ticket_outbound_messages_status_next_retry", "ticket_outbound_messages"),
        ("ix_tickets_team_status", "tickets"),
        ("ix_tickets_status_updated_at", "tickets"),
    ]:
        try:
            op.drop_index(name, table_name=table)
        except Exception:
            pass
