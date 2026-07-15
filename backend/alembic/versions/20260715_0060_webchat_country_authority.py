"""add country authority to public WebChat origin bindings

Revision ID: 20260715_0060
Revises: 20260713_0059
Create Date: 2026-07-15

The column is nullable so historical bindings are not guessed or backfilled.
Only bindings explicitly assigned a country can project country-scoped tickets.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260715_0060"
down_revision = "20260713_0059"
branch_labels = None
depends_on = None

_TABLE = "webchat_public_origin_bindings"
_COUNTRY_INDEX = "ix_webchat_public_origin_bindings_country_code"
_SCOPE_INDEX = "ix_webchat_public_origin_binding_scope"


def _recreate_mode(bind) -> str:
    return "always" if bind.dialect.name == "sqlite" else "auto"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        raise RuntimeError(f"required table missing: {_TABLE}")

    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    with op.batch_alter_table(_TABLE, recreate=_recreate_mode(bind)) as batch:
        if _SCOPE_INDEX in indexes:
            batch.drop_index(_SCOPE_INDEX)
        if "country_code" not in columns:
            batch.add_column(sa.Column("country_code", sa.String(length=8), nullable=True))
        if _COUNTRY_INDEX not in indexes:
            batch.create_index(_COUNTRY_INDEX, ["country_code"])
        batch.create_index(
            _SCOPE_INDEX,
            ["tenant_key", "country_code", "channel_key", "is_active"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    with op.batch_alter_table(_TABLE, recreate=_recreate_mode(bind)) as batch:
        if _SCOPE_INDEX in indexes:
            batch.drop_index(_SCOPE_INDEX)
        if _COUNTRY_INDEX in indexes:
            batch.drop_index(_COUNTRY_INDEX)
        batch.create_index(
            _SCOPE_INDEX,
            ["tenant_key", "channel_key", "is_active"],
        )
        if "country_code" in columns:
            batch.drop_column("country_code")
