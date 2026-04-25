"""round b webchat conversations and messages

Revision ID: 20260425_round_b_webchat
Revises: 20260421_gov_r4
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = '20260425_round_b_webchat'
down_revision = '20260421_gov_r4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'webchat_conversations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', sa.String(length=64), nullable=False),
        sa.Column('visitor_token_hash', sa.String(length=96), nullable=False),
        sa.Column('tenant_key', sa.String(length=120), nullable=False, server_default='default'),
        sa.Column('channel_key', sa.String(length=120), nullable=False, server_default='default'),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id'), nullable=False),
        sa.Column('visitor_name', sa.String(length=160), nullable=True),
        sa.Column('visitor_email', sa.String(length=200), nullable=True),
        sa.Column('visitor_phone', sa.String(length=80), nullable=True),
        sa.Column('visitor_ref', sa.String(length=160), nullable=True),
        sa.Column('origin', sa.String(length=255), nullable=True),
        sa.Column('page_url', sa.String(length=700), nullable=True),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('status', sa.String(length=40), nullable=False, server_default='open'),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_key', 'channel_key', 'public_id', name='uq_webchat_tenant_channel_public'),
    )
    op.create_index('ix_webchat_conversations_public_id', 'webchat_conversations', ['public_id'])
    op.create_index('ix_webchat_conversations_ticket_id', 'webchat_conversations', ['ticket_id'])
    op.create_index('ix_webchat_conversations_tenant_key', 'webchat_conversations', ['tenant_key'])
    op.create_index('ix_webchat_conversations_channel_key', 'webchat_conversations', ['channel_key'])
    op.create_index('ix_webchat_conversations_visitor_ref', 'webchat_conversations', ['visitor_ref'])
    op.create_index('ix_webchat_conversations_origin', 'webchat_conversations', ['origin'])
    op.create_index('ix_webchat_conversations_status', 'webchat_conversations', ['status'])
    op.create_index('ix_webchat_conversations_last_seen_at', 'webchat_conversations', ['last_seen_at'])
    op.create_index('ix_webchat_conversations_created_at', 'webchat_conversations', ['created_at'])
    op.create_index('ix_webchat_conversations_updated_at', 'webchat_conversations', ['updated_at'])
    op.create_index('ix_webchat_conversations_visitor_token_hash', 'webchat_conversations', ['visitor_token_hash'])

    op.create_table(
        'webchat_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('webchat_conversations.id'), nullable=False),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id'), nullable=False),
        sa.Column('direction', sa.String(length=24), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('author_label', sa.String(length=120), nullable=True),
        sa.Column('safety_level', sa.String(length=40), nullable=True),
        sa.Column('safety_reasons_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_webchat_messages_conversation_id', 'webchat_messages', ['conversation_id'])
    op.create_index('ix_webchat_messages_ticket_id', 'webchat_messages', ['ticket_id'])
    op.create_index('ix_webchat_messages_direction', 'webchat_messages', ['direction'])
    op.create_index('ix_webchat_messages_created_at', 'webchat_messages', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_webchat_messages_created_at', table_name='webchat_messages')
    op.drop_index('ix_webchat_messages_direction', table_name='webchat_messages')
    op.drop_index('ix_webchat_messages_ticket_id', table_name='webchat_messages')
    op.drop_index('ix_webchat_messages_conversation_id', table_name='webchat_messages')
    op.drop_table('webchat_messages')
    op.drop_index('ix_webchat_conversations_visitor_token_hash', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_updated_at', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_created_at', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_last_seen_at', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_status', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_origin', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_visitor_ref', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_channel_key', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_tenant_key', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_ticket_id', table_name='webchat_conversations')
    op.drop_index('ix_webchat_conversations_public_id', table_name='webchat_conversations')
    op.drop_table('webchat_conversations')
