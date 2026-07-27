"""Merge R4 scope with WhatsApp and add the non-routable binding state.

Revision ID: 20260727_wa2
Revises: 20260727_r4p0c, 20260727_wa1
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op


revision = "20260727_wa2"
down_revision = ("20260727_r4p0c", "20260727_wa1")
branch_labels = None
depends_on = None


_CONSTRAINT = "ck_whatsapp_connection_desired_state"
_ACTIVE_STATES = "desired_state IN ('disabled','binding','active')"
_LEGACY_STATES = "desired_state IN ('disabled','active')"


def upgrade() -> None:
    with op.batch_alter_table("whatsapp_connections") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _ACTIVE_STATES)


def downgrade() -> None:
    with op.batch_alter_table("whatsapp_connections") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _LEGACY_STATES)
