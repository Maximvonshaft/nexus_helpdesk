"""round8 openclaw + markets

Revision ID: 20260410_0005
Revises: 20260410_0004
Create Date: 2026-04-10 00:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0005"
down_revision = "20260410_0004"
branch_labels = None
depends_on = None


def _tables(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _columns(inspector, table_name: str) -> set[str]:
    if table_name not in _tables(inspector):
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    if table_name not in _tables(inspector):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    if 'markets' not in tables:
        op.create_table(
            'markets',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code', sa.String(length=16), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('country_code', sa.String(length=8), nullable=False),
            sa.Column('language_code', sa.String(length=16), nullable=True),
            sa.Column('timezone', sa.String(length=64), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('code', name='uq_markets_code'),
            sa.UniqueConstraint('name', name='uq_markets_name'),
        )
        inspector = sa.inspect(bind)
    if 'ix_markets_code' not in _indexes(inspector, 'markets'):
        op.create_index('ix_markets_code', 'markets', ['code'], unique=True)
    if 'ix_markets_name' not in _indexes(inspector, 'markets'):
        op.create_index('ix_markets_name', 'markets', ['name'], unique=True)
    if 'ix_markets_country_code' not in _indexes(inspector, 'markets'):
        op.create_index('ix_markets_country_code', 'markets', ['country_code'])

    team_cols = _columns(inspector, 'teams')
    if 'market_id' not in team_cols:
        op.add_column('teams', sa.Column('market_id', sa.Integer(), nullable=True))
        if bind.dialect.name != 'sqlite':
            op.create_foreign_key('fk_teams_market_id_markets', 'teams', 'markets', ['market_id'], ['id'])
        inspector = sa.inspect(bind)
    if 'ix_teams_market_id' not in _indexes(inspector, 'teams'):
        op.create_index('ix_teams_market_id', 'teams', ['market_id'])

    ticket_cols = _columns(inspector, 'tickets')
    if 'market_id' not in ticket_cols:
        op.add_column('tickets', sa.Column('market_id', sa.Integer(), nullable=True))
        if bind.dialect.name != 'sqlite':
            op.create_foreign_key('fk_tickets_market_id_markets', 'tickets', 'markets', ['market_id'], ['id'])
        inspector = sa.inspect(bind)
    if 'country_code' not in ticket_cols:
        op.add_column('tickets', sa.Column('country_code', sa.String(length=8), nullable=True))
        inspector = sa.inspect(bind)
    if 'ix_tickets_market_id' not in _indexes(inspector, 'tickets'):
        op.create_index('ix_tickets_market_id', 'tickets', ['market_id'])
    if 'ix_tickets_country_code' not in _indexes(inspector, 'tickets'):
        op.create_index('ix_tickets_country_code', 'tickets', ['country_code'])

    tables = _tables(inspector)
    if 'openclaw_conversation_links' not in tables:
        op.create_table(
            'openclaw_conversation_links',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('ticket_id', sa.Integer(), nullable=False),
            sa.Column('session_key', sa.String(length=255), nullable=False),
            sa.Column('channel', sa.String(length=60), nullable=True),
            sa.Column('recipient', sa.String(length=255), nullable=True),
            sa.Column('account_id', sa.String(length=120), nullable=True),
            sa.Column('thread_id', sa.String(length=120), nullable=True),
            sa.Column('route_json', sa.JSON(), nullable=True),
            sa.Column('last_cursor', sa.Integer(), nullable=True),
            sa.Column('last_message_id', sa.String(length=255), nullable=True),
            sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id']),
            sa.UniqueConstraint('session_key', name='uq_openclaw_session_key'),
            sa.UniqueConstraint('ticket_id', name='uq_openclaw_ticket_link'),
        )
        inspector = sa.inspect(bind)
    if 'ix_openclaw_conversation_links_session_key' not in _indexes(inspector, 'openclaw_conversation_links'):
        op.create_index('ix_openclaw_conversation_links_session_key', 'openclaw_conversation_links', ['session_key'])
    if 'ix_openclaw_conversation_links_channel' not in _indexes(inspector, 'openclaw_conversation_links'):
        op.create_index('ix_openclaw_conversation_links_channel', 'openclaw_conversation_links', ['channel'])
    if 'ix_openclaw_conversation_links_recipient' not in _indexes(inspector, 'openclaw_conversation_links'):
        op.create_index('ix_openclaw_conversation_links_recipient', 'openclaw_conversation_links', ['recipient'])
    if 'ix_openclaw_conversation_links_last_synced_at' not in _indexes(inspector, 'openclaw_conversation_links'):
        op.create_index('ix_openclaw_conversation_links_last_synced_at', 'openclaw_conversation_links', ['last_synced_at'])

    if 'openclaw_transcript_messages' not in tables:
        op.create_table(
            'openclaw_transcript_messages',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('conversation_id', sa.Integer(), nullable=False),
            sa.Column('ticket_id', sa.Integer(), nullable=False),
            sa.Column('session_key', sa.String(length=255), nullable=False),
            sa.Column('message_id', sa.String(length=255), nullable=False),
            sa.Column('role', sa.String(length=32), nullable=True),
            sa.Column('author_name', sa.String(length=160), nullable=True),
            sa.Column('body_text', sa.Text(), nullable=True),
            sa.Column('content_json', sa.JSON(), nullable=True),
            sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['conversation_id'], ['openclaw_conversation_links.id']),
            sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id']),
            sa.UniqueConstraint('conversation_id', 'message_id', name='uq_openclaw_conversation_message'),
        )
        inspector = sa.inspect(bind)
    if 'ix_openclaw_transcript_messages_session_key' not in _indexes(inspector, 'openclaw_transcript_messages'):
        op.create_index('ix_openclaw_transcript_messages_session_key', 'openclaw_transcript_messages', ['session_key'])
    if 'ix_openclaw_transcript_messages_message_id' not in _indexes(inspector, 'openclaw_transcript_messages'):
        op.create_index('ix_openclaw_transcript_messages_message_id', 'openclaw_transcript_messages', ['message_id'])
    if 'ix_openclaw_transcript_messages_role' not in _indexes(inspector, 'openclaw_transcript_messages'):
        op.create_index('ix_openclaw_transcript_messages_role', 'openclaw_transcript_messages', ['role'])
    if 'ix_openclaw_transcript_messages_received_at' not in _indexes(inspector, 'openclaw_transcript_messages'):
        op.create_index('ix_openclaw_transcript_messages_received_at', 'openclaw_transcript_messages', ['received_at'])


def downgrade() -> None:
    for name, table in [
        ('ix_openclaw_transcript_messages_received_at', 'openclaw_transcript_messages'),
        ('ix_openclaw_transcript_messages_role', 'openclaw_transcript_messages'),
        ('ix_openclaw_transcript_messages_message_id', 'openclaw_transcript_messages'),
        ('ix_openclaw_transcript_messages_session_key', 'openclaw_transcript_messages'),
        ('ix_openclaw_conversation_links_last_synced_at', 'openclaw_conversation_links'),
        ('ix_openclaw_conversation_links_recipient', 'openclaw_conversation_links'),
        ('ix_openclaw_conversation_links_channel', 'openclaw_conversation_links'),
        ('ix_openclaw_conversation_links_session_key', 'openclaw_conversation_links'),
        ('ix_tickets_country_code', 'tickets'),
        ('ix_tickets_market_id', 'tickets'),
        ('ix_teams_market_id', 'teams'),
        ('ix_markets_country_code', 'markets'),
        ('ix_markets_name', 'markets'),
        ('ix_markets_code', 'markets'),
    ]:
        try:
            op.drop_index(name, table_name=table)
        except Exception:
            pass
    for table_name in ['openclaw_transcript_messages', 'openclaw_conversation_links', 'markets']:
        try:
            op.drop_table(table_name)
        except Exception:
            pass
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        for name, table_name in [
            ('fk_tickets_market_id_markets', 'tickets'),
            ('fk_teams_market_id_markets', 'teams'),
        ]:
            try:
                op.drop_constraint(name, table_name, type_='foreignkey')
            except Exception:
                pass
    for table_name, column_name in [('tickets', 'country_code'), ('tickets', 'market_id'), ('teams', 'market_id')]:
        try:
            op.drop_column(table_name, column_name)
        except Exception:
            pass
