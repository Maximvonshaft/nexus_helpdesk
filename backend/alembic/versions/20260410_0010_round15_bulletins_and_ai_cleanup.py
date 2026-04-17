"""round15 bulletins and ai cleanup

Revision ID: 20260410_0010
Revises: 20260410_0008
Create Date: 2026-04-10 00:09:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260410_0010"
down_revision = "20260410_0009"
branch_labels = None
depends_on = None


def _tables(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _columns(inspector, table_name: str) -> set[str]:
    if table_name not in _tables(inspector):
        return set()
    return {c['name'] for c in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    if table_name not in _tables(inspector):
        return set()
    return {idx['name'] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)
    if 'market_bulletins' not in tables:
        op.create_table(
            'market_bulletins',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('market_id', sa.Integer(), sa.ForeignKey('markets.id'), nullable=True),
            sa.Column('country_code', sa.String(length=8), nullable=True),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('category', sa.String(length=60), nullable=False, server_default='notice'),
            sa.Column('channels_csv', sa.String(length=255), nullable=True),
            sa.Column('audience', sa.String(length=60), nullable=False, server_default='customer'),
            sa.Column('severity', sa.String(length=40), nullable=False, server_default='info'),
            sa.Column('auto_inject_to_ai', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        inspector = sa.inspect(bind)
    if 'ix_market_bulletins_market_id' not in _indexes(inspector, 'market_bulletins'):
        op.create_index('ix_market_bulletins_market_id', 'market_bulletins', ['market_id'], unique=False)
    if 'ix_market_bulletins_country_code' not in _indexes(inspector, 'market_bulletins'):
        op.create_index('ix_market_bulletins_country_code', 'market_bulletins', ['country_code'], unique=False)
    if 'ix_market_bulletins_active_window' not in _indexes(inspector, 'market_bulletins'):
        op.create_index('ix_market_bulletins_active_window', 'market_bulletins', ['is_active', 'starts_at', 'ends_at'], unique=False)

    if 'ticket_ai_intakes' not in _tables(inspector):
        op.create_table(
            'ticket_ai_intakes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id'), nullable=False),
            sa.Column('summary', sa.Text(), nullable=False),
            sa.Column('classification', sa.String(length=120), nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('missing_fields_json', sa.Text(), nullable=True),
            sa.Column('recommended_action', sa.Text(), nullable=True),
            sa.Column('suggested_reply', sa.Text(), nullable=True),
            sa.Column('raw_payload_json', sa.Text(), nullable=True),
            sa.Column('human_override_reason', sa.Text(), nullable=True),
            sa.Column('market_id', sa.Integer(), sa.ForeignKey('markets.id'), nullable=True),
            sa.Column('country_code', sa.String(length=8), nullable=True),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        inspector = sa.inspect(bind)
    cols = _columns(inspector, 'ticket_ai_intakes')
    if 'market_id' not in cols:
        if bind.dialect.name == 'sqlite':
            op.add_column('ticket_ai_intakes', sa.Column('market_id', sa.Integer(), nullable=True))
        else:
            op.add_column('ticket_ai_intakes', sa.Column('market_id', sa.Integer(), sa.ForeignKey('markets.id'), nullable=True))
        inspector = sa.inspect(bind)
        cols = _columns(inspector, 'ticket_ai_intakes')
    if 'country_code' not in cols:
        op.add_column('ticket_ai_intakes', sa.Column('country_code', sa.String(length=8), nullable=True))
        inspector = sa.inspect(bind)
    if 'ix_ticket_ai_intakes_market_id' not in _indexes(inspector, 'ticket_ai_intakes'):
        op.create_index('ix_ticket_ai_intakes_market_id', 'ticket_ai_intakes', ['market_id'], unique=False)
    if 'ix_ticket_ai_intakes_country_code' not in _indexes(inspector, 'ticket_ai_intakes'):
        op.create_index('ix_ticket_ai_intakes_country_code', 'ticket_ai_intakes', ['country_code'], unique=False)


def downgrade() -> None:
    pass
