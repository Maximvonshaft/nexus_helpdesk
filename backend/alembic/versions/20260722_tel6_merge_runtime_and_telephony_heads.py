"""merge canonical runtime-convergence and telephony heads

Revision ID: 20260722_tel6
Revises: 20260722_0074, 20260722_tel5
Create Date: 2026-07-22
"""

from __future__ import annotations

revision = "20260722_tel6"
down_revision = ("20260722_0074", "20260722_tel5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join two independent, already-applied canonical migration branches."""


def downgrade() -> None:
    """Return to the two independent heads without mutating application data."""
