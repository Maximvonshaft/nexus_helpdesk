"""Add the canonical BackgroundJob execution scope envelope.

Revision ID: 20260727_aud8
Revises: 20260727_aud7
Create Date: 2026-07-27
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud8"
down_revision = "20260727_aud7"
branch_labels = None
depends_on = None

_PURPOSE_BY_JOB_TYPE = {
    "webchat.ai_reply": "automated_ai",
    "webchat.handoff_snapshot": "human_support",
    "speedaf.work_order.create": "provider_tool_execution",
    "speedaf.address_update.submit": "provider_tool_execution",
    "speedaf.voice.callback": "provider_tool_execution",
    "email.mailbox_sync": "human_support",
}


def _mapping(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _first(bind, sql: str, **params):
    return bind.execute(sa.text(sql), params).first()


def _tenant_from_key(bind, tenant_key: Any) -> int | None:
    normalized = str(tenant_key or "").strip()
    if not normalized or normalized == "default":
        return None
    row = _first(
        bind,
        "SELECT id FROM tenants WHERE tenant_key = :tenant_key AND is_active = true",
        tenant_key=normalized,
    )
    return int(row[0]) if row else None


def _ticket_scope(bind, ticket_id: int) -> tuple[int | None, int | None]:
    row = _first(
        bind,
        "SELECT tenant_id, customer_id FROM tickets WHERE id = :ticket_id",
        ticket_id=ticket_id,
    )
    if not row:
        return None, None
    return (
        int(row[0]) if row[0] is not None else None,
        int(row[1]) if row[1] is not None else None,
    )


def _conversation_scope(
    bind,
    conversation_id: int,
) -> tuple[int | None, int | None, int | None]:
    row = _first(
        bind,
        "SELECT tenant_key, ticket_id FROM webchat_conversations WHERE id = :conversation_id",
        conversation_id=conversation_id,
    )
    if not row:
        return None, None, None
    tenant_key, ticket_id = row[0], row[1]
    if ticket_id is not None:
        tenant_id, customer_id = _ticket_scope(bind, int(ticket_id))
        return tenant_id, customer_id, int(ticket_id)
    tenant_id = _tenant_from_key(bind, tenant_key)
    control = _first(
        bind,
        "SELECT customer_id FROM conversation_controls WHERE conversation_id = :conversation_id",
        conversation_id=conversation_id,
    )
    customer_id = int(control[0]) if control and control[0] is not None else None
    return tenant_id, customer_id, None


def _customer_tenant(bind, customer_id: int) -> int | None:
    row = _first(
        bind,
        "SELECT tenant_id FROM customers WHERE id = :customer_id",
        customer_id=customer_id,
    )
    return int(row[0]) if row and row[0] is not None else None


def _active_tenant(bind, tenant_id: int) -> int | None:
    row = _first(
        bind,
        "SELECT id FROM tenants WHERE id = :tenant_id AND is_active = true",
        tenant_id=tenant_id,
    )
    return int(row[0]) if row else None


def _email_account_tenant(bind, account_id: int) -> int | None:
    row = _first(
        bind,
        """
        SELECT m.tenant_id
        FROM outbound_email_accounts AS a
        JOIN markets AS m ON m.id = a.market_id
        WHERE a.id = :account_id
        """,
        account_id=account_id,
    )
    return int(row[0]) if row and row[0] is not None else None


def _channel_account_tenant(bind, account_id: int) -> int | None:
    row = _first(
        bind,
        "SELECT tenant_id FROM channel_accounts WHERE id = :account_id",
        account_id=account_id,
    )
    return int(row[0]) if row and row[0] is not None else None


def _scope_for_job(bind, job_type: str, payload_json: Any) -> dict[str, Any]:
    payload = _mapping(payload_json)
    tenant_candidates: set[int] = set()
    customer_candidates: set[int] = set()
    resource_type: str | None = None
    resource_id: str | None = None

    explicit_tenant_id = _positive_int(payload.get("tenant_id"))
    if explicit_tenant_id is not None:
        active = _active_tenant(bind, explicit_tenant_id)
        if active is not None:
            tenant_candidates.add(active)

    tenant_key_id = _tenant_from_key(bind, payload.get("tenant_key"))
    if tenant_key_id is not None:
        tenant_candidates.add(tenant_key_id)

    ticket_id = _positive_int(payload.get("ticket_id"))
    if ticket_id is not None:
        tenant_id, customer_id = _ticket_scope(bind, ticket_id)
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if customer_id is not None:
            customer_candidates.add(customer_id)
        resource_type, resource_id = "ticket", str(ticket_id)

    conversation_id = _positive_int(payload.get("conversation_id"))
    if conversation_id is not None:
        tenant_id, customer_id, _linked_ticket_id = _conversation_scope(
            bind,
            conversation_id,
        )
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if customer_id is not None:
            customer_candidates.add(customer_id)
        if resource_type is None:
            resource_type, resource_id = "conversation", str(conversation_id)

    customer_id = _positive_int(payload.get("customer_id"))
    if customer_id is not None:
        customer_tenant_id = _customer_tenant(bind, customer_id)
        if customer_tenant_id is not None:
            tenant_candidates.add(customer_tenant_id)
            customer_candidates.add(customer_id)
        if resource_type is None:
            resource_type, resource_id = "customer", str(customer_id)

    account_id = _positive_int(payload.get("account_id"))
    if account_id is not None and job_type == "email.mailbox_sync":
        tenant_id = _email_account_tenant(bind, account_id)
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if resource_type is None:
            resource_type, resource_id = "outbound_email_account", str(account_id)

    channel_account_id = _positive_int(payload.get("channel_account_id"))
    if channel_account_id is not None:
        tenant_id = _channel_account_tenant(bind, channel_account_id)
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if resource_type is None:
            resource_type, resource_id = "channel_account", str(channel_account_id)

    purpose = _PURPOSE_BY_JOB_TYPE.get(job_type, "unclassified")
    if len(tenant_candidates) != 1:
        return {
            "scope_type": "unresolved",
            "tenant_id": None,
            "customer_id": None,
            "purpose": purpose,
            "resource_type": resource_type,
            "resource_id": resource_id,
        }

    tenant_id = next(iter(tenant_candidates))
    valid_customers = {
        candidate
        for candidate in customer_candidates
        if _customer_tenant(bind, candidate) == tenant_id
    }
    resolved_customer_id = (
        next(iter(valid_customers)) if len(valid_customers) == 1 else None
    )
    return {
        "scope_type": "tenant",
        "tenant_id": tenant_id,
        "customer_id": resolved_customer_id,
        "purpose": purpose,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }


def upgrade() -> None:
    op.create_table(
        "background_job_scopes",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.String(length=180), nullable=True),
        sa.Column("source_schema", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('tenant','platform','unresolved')",
            name="ck_background_job_scope_type",
        ),
        sa.CheckConstraint(
            "scope_type <> 'tenant' OR tenant_id IS NOT NULL",
            name="ck_background_job_scope_tenant_required",
        ),
        sa.CheckConstraint(
            "scope_type = 'tenant' OR tenant_id IS NULL",
            name="ck_background_job_scope_non_tenant_has_no_tenant",
        ),
        sa.CheckConstraint(
            "length(trim(purpose)) > 0",
            name="ck_background_job_scope_purpose_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["background_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_background_job_scopes_scope_type",
        "background_job_scopes",
        ["scope_type"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_scopes_tenant_id",
        "background_job_scopes",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_scopes_customer_id",
        "background_job_scopes",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_scopes_created_at",
        "background_job_scopes",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_scope_tenant_purpose",
        "background_job_scopes",
        ["tenant_id", "purpose", "job_id"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_scope_customer_purpose",
        "background_job_scopes",
        ["customer_id", "purpose", "job_id"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_scope_resource",
        "background_job_scopes",
        ["resource_type", "resource_id"],
        unique=False,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, job_type, payload_json FROM background_jobs ORDER BY id"
        )
    ).all()
    scope_table = sa.table(
        "background_job_scopes",
        sa.column("job_id", sa.Integer()),
        sa.column("scope_type", sa.String()),
        sa.column("tenant_id", sa.Integer()),
        sa.column("customer_id", sa.Integer()),
        sa.column("purpose", sa.String()),
        sa.column("resource_type", sa.String()),
        sa.column("resource_id", sa.String()),
        sa.column("source_schema", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for job_id, job_type, payload_json in rows:
        scope = _scope_for_job(bind, str(job_type), payload_json)
        bind.execute(
            scope_table.insert().values(
                job_id=int(job_id),
                **scope,
                source_schema="nexus.background-job-scope.v1",
                created_at=sa.func.current_timestamp(),
                updated_at=sa.func.current_timestamp(),
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_background_job_scope_resource",
        table_name="background_job_scopes",
    )
    op.drop_index(
        "ix_background_job_scope_customer_purpose",
        table_name="background_job_scopes",
    )
    op.drop_index(
        "ix_background_job_scope_tenant_purpose",
        table_name="background_job_scopes",
    )
    op.drop_index(
        "ix_background_job_scopes_created_at",
        table_name="background_job_scopes",
    )
    op.drop_index(
        "ix_background_job_scopes_customer_id",
        table_name="background_job_scopes",
    )
    op.drop_index(
        "ix_background_job_scopes_tenant_id",
        table_name="background_job_scopes",
    )
    op.drop_index(
        "ix_background_job_scopes_scope_type",
        table_name="background_job_scopes",
    )
    op.drop_table("background_job_scopes")
