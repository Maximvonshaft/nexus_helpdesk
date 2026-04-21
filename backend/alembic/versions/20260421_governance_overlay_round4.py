"""governance overlay round 4 support tables

Revision ID: 20260421_governance_overlay_round4
Revises: 20260410_0001
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa

revision = '20260421_governance_overlay_round4'
down_revision = '20260410_0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'admin_audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(length=120), nullable=False),
        sa.Column('target_type', sa.String(length=80), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('old_value_json', sa.Text(), nullable=True),
        sa.Column('new_value_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_admin_audit_logs_actor_id', 'admin_audit_logs', ['actor_id'])
    op.create_index('ix_admin_audit_logs_action', 'admin_audit_logs', ['action'])
    op.create_index('ix_admin_audit_logs_target_type', 'admin_audit_logs', ['target_type'])
    op.create_index('ix_admin_audit_logs_created_at', 'admin_audit_logs', ['created_at'])

    op.create_table(
        'openclaw_unresolved_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('session_key', sa.String(length=255), nullable=True),
        sa.Column('event_type', sa.String(length=80), nullable=True),
        sa.Column('recipient', sa.String(length=255), nullable=True),
        sa.Column('source_chat_id', sa.String(length=120), nullable=True),
        sa.Column('preferred_reply_contact', sa.String(length=160), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=40), nullable=False, server_default='pending'),
        sa.Column('replay_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_openclaw_unresolved_events_status', 'openclaw_unresolved_events', ['status'])
    op.create_index('ix_openclaw_unresolved_events_session_key', 'openclaw_unresolved_events', ['session_key'])
    op.create_index('ix_openclaw_unresolved_events_recipient', 'openclaw_unresolved_events', ['recipient'])
    op.create_index('ix_openclaw_unresolved_events_source_chat_id', 'openclaw_unresolved_events', ['source_chat_id'])
    op.create_index('ix_openclaw_unresolved_events_created_at', 'openclaw_unresolved_events', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_openclaw_unresolved_events_created_at', table_name='openclaw_unresolved_events')
    op.drop_index('ix_openclaw_unresolved_events_source_chat_id', table_name='openclaw_unresolved_events')
    op.drop_index('ix_openclaw_unresolved_events_recipient', table_name='openclaw_unresolved_events')
    op.drop_index('ix_openclaw_unresolved_events_session_key', table_name='openclaw_unresolved_events')
    op.drop_index('ix_openclaw_unresolved_events_status', table_name='openclaw_unresolved_events')
    op.drop_table('openclaw_unresolved_events')
    op.drop_index('ix_admin_audit_logs_created_at', table_name='admin_audit_logs')
    op.drop_index('ix_admin_audit_logs_target_type', table_name='admin_audit_logs')
    op.drop_index('ix_admin_audit_logs_action', table_name='admin_audit_logs')
    op.drop_index('ix_admin_audit_logs_actor_id', table_name='admin_audit_logs')
    op.drop_table('admin_audit_logs')
