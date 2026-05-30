"""email mailbox polling fields

Revision ID: 20260530_0045
Revises: 20260530_0044
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260530_0045"
down_revision = "20260530_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbound_email_accounts", sa.Column("inbound_enabled", sa.Boolean(), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_host", sa.String(length=253), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_port", sa.Integer(), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_username", sa.String(length=255), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_password_encrypted", sa.Text(), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_security_mode", sa.String(length=20), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_mailbox", sa.String(length=120), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_sync_cursor", sa.String(length=255), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_last_status", sa.String(length=40), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_last_error", sa.Text(), nullable=True))
    op.add_column("outbound_email_accounts", sa.Column("imap_last_sync_job_id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE outbound_email_accounts SET inbound_enabled = 0 WHERE inbound_enabled IS NULL"))
    op.alter_column("outbound_email_accounts", "inbound_enabled", nullable=False)
    op.create_index("ix_outbound_email_accounts_inbound_enabled", "outbound_email_accounts", ["inbound_enabled"], unique=False)
    op.create_index("ix_outbound_email_accounts_imap_host", "outbound_email_accounts", ["imap_host"], unique=False)
    op.create_index("ix_outbound_email_accounts_imap_security_mode", "outbound_email_accounts", ["imap_security_mode"], unique=False)
    op.create_index("ix_outbound_email_accounts_imap_last_seen_at", "outbound_email_accounts", ["imap_last_seen_at"], unique=False)
    op.create_index("ix_outbound_email_accounts_imap_last_status", "outbound_email_accounts", ["imap_last_status"], unique=False)
    op.create_index("ix_outbound_email_accounts_imap_last_sync_job_id", "outbound_email_accounts", ["imap_last_sync_job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_outbound_email_accounts_imap_last_sync_job_id", table_name="outbound_email_accounts")
    op.drop_index("ix_outbound_email_accounts_imap_last_status", table_name="outbound_email_accounts")
    op.drop_index("ix_outbound_email_accounts_imap_last_seen_at", table_name="outbound_email_accounts")
    op.drop_index("ix_outbound_email_accounts_imap_security_mode", table_name="outbound_email_accounts")
    op.drop_index("ix_outbound_email_accounts_imap_host", table_name="outbound_email_accounts")
    op.drop_index("ix_outbound_email_accounts_inbound_enabled", table_name="outbound_email_accounts")
    op.drop_column("outbound_email_accounts", "imap_last_sync_job_id")
    op.drop_column("outbound_email_accounts", "imap_last_error")
    op.drop_column("outbound_email_accounts", "imap_last_status")
    op.drop_column("outbound_email_accounts", "imap_last_seen_at")
    op.drop_column("outbound_email_accounts", "imap_sync_cursor")
    op.drop_column("outbound_email_accounts", "imap_mailbox")
    op.drop_column("outbound_email_accounts", "imap_security_mode")
    op.drop_column("outbound_email_accounts", "imap_password_encrypted")
    op.drop_column("outbound_email_accounts", "imap_username")
    op.drop_column("outbound_email_accounts", "imap_port")
    op.drop_column("outbound_email_accounts", "imap_host")
    op.drop_column("outbound_email_accounts", "inbound_enabled")
