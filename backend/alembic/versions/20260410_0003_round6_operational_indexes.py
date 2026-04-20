"""round6 operational indexes

Revision ID: 20260410_0003
Revises: 20260410_0002
Create Date: 2026-04-10 22:00:00
"""

from alembic import op

revision = '20260410_0003'
down_revision = '20260410_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_ticket_outbound_messages_claim', 'ticket_outbound_messages', ['status', 'next_retry_at', 'locked_at', 'created_at'], unique=False)
    op.create_index('ix_background_jobs_claim', 'background_jobs', ['status', 'next_run_at', 'locked_at', 'created_at'], unique=False)
    op.create_index('ix_user_capability_overrides_lookup', 'user_capability_overrides', ['user_id', 'capability'], unique=False)
    op.create_index('ix_integration_request_logs_window', 'integration_request_logs', ['client_id', 'endpoint', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_integration_request_logs_window', table_name='integration_request_logs')
    op.drop_index('ix_user_capability_overrides_lookup', table_name='user_capability_overrides')
    op.drop_index('ix_background_jobs_claim', table_name='background_jobs')
    op.drop_index('ix_ticket_outbound_messages_claim', table_name='ticket_outbound_messages')
