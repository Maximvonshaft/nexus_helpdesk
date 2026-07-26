"""Name the deferred WebChat message-to-AI-turn back-reference.

Revision ID: 20260727_aud1
Revises: 20260723_tel6
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud1"
down_revision = "20260723_tel6"
branch_labels = None
depends_on = None

_TABLE = "webchat_messages"
_COLUMNS = ("ai_turn_id",)
_REFERRED_TABLE = "webchat_ai_turns"
_CANONICAL_NAME = "fk_webchat_messages_ai_turn_id"
_LEGACY_NAME = "webchat_messages_ai_turn_id_fkey"


def _matching_constraint_name(bind) -> str | None:
    inspector = sa.inspect(bind)
    for constraint in inspector.get_foreign_keys(_TABLE):
        constrained = tuple(constraint.get("constrained_columns") or ())
        referred = str(constraint.get("referred_table") or "")
        if constrained == _COLUMNS and referred == _REFERRED_TABLE:
            name = constraint.get("name")
            return str(name) if name else None
    return None


def _rename_constraint(bind, *, source: str, target: str) -> None:
    preparer = bind.dialect.identifier_preparer
    bind.execute(
        sa.text(
            "ALTER TABLE "
            f"{preparer.quote(_TABLE)} RENAME CONSTRAINT "
            f"{preparer.quote(source)} TO {preparer.quote(target)}"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    observed = _matching_constraint_name(bind)
    if observed is None or observed == _CANONICAL_NAME:
        return
    _rename_constraint(bind, source=observed, target=_CANONICAL_NAME)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    observed = _matching_constraint_name(bind)
    if observed != _CANONICAL_NAME:
        return
    _rename_constraint(bind, source=_CANONICAL_NAME, target=_LEGACY_NAME)
