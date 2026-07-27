from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ..models import (
    BackgroundJob,
    ChannelAccount,
    Customer,
    Market,
    OutboundEmailAccount,
    Tenant,
    Ticket,
)
from ..models_agent_routing import ConversationControl
from ..models_job_scope import BackgroundJobScope
from ..webchat_models import WebchatConversation

SCOPE_SCHEMA = "nexus.background-job-scope.v1"
PURPOSE_BY_JOB_TYPE = {
    "webchat.ai_reply": "automated_ai",
    "webchat.handoff_snapshot": "human_support",
    "speedaf.work_order.create": "provider_tool_execution",
    "speedaf.address_update.submit": "provider_tool_execution",
    "speedaf.voice.callback": "provider_tool_execution",
    "email.mailbox_sync": "human_support",
}
# Platform work must be explicitly listed. An unknown or unowned Job is never
# silently elevated into platform scope.
PLATFORM_JOB_TYPES: frozenset[str] = frozenset()
_INSTALLED = False


@dataclass(frozen=True)
class JobScopeValues:
    scope_type: str
    tenant_id: int | None
    customer_id: int | None
    purpose: str
    resource_type: str | None
    resource_id: str | None


def _payload(job: BackgroundJob) -> dict[str, Any]:
    try:
        value = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _first(connection: Connection, statement):
    return connection.execute(statement).first()


def _tenant_for_key(connection: Connection, tenant_key: str | None) -> int | None:
    normalized = str(tenant_key or "").strip()
    if not normalized or normalized == "default":
        return None
    row = _first(
        connection,
        select(Tenant.id).where(
            Tenant.tenant_key == normalized,
            Tenant.is_active.is_(True),
        ),
    )
    return int(row[0]) if row else None


def _ticket_scope(
    connection: Connection,
    ticket_id: int,
) -> tuple[int | None, int | None]:
    row = _first(
        connection,
        select(Ticket.tenant_id, Ticket.customer_id).where(Ticket.id == ticket_id),
    )
    if not row:
        return None, None
    return (
        int(row[0]) if row[0] is not None else None,
        int(row[1]) if row[1] is not None else None,
    )


def _conversation_scope(
    connection: Connection,
    conversation_id: int,
) -> tuple[int | None, int | None, int | None]:
    row = _first(
        connection,
        select(
            WebchatConversation.tenant_key,
            WebchatConversation.ticket_id,
        ).where(WebchatConversation.id == conversation_id),
    )
    if not row:
        return None, None, None
    tenant_key, ticket_id = row[0], row[1]
    if ticket_id is not None:
        tenant_id, customer_id = _ticket_scope(connection, int(ticket_id))
        return tenant_id, customer_id, int(ticket_id)
    tenant_id = _tenant_for_key(connection, str(tenant_key or ""))
    control = _first(
        connection,
        select(ConversationControl.customer_id).where(
            ConversationControl.conversation_id == conversation_id
        ),
    )
    customer_id = int(control[0]) if control and control[0] is not None else None
    return tenant_id, customer_id, None


def _customer_scope(
    connection: Connection,
    customer_id: int,
) -> int | None:
    row = _first(
        connection,
        select(Customer.tenant_id).where(Customer.id == customer_id),
    )
    return int(row[0]) if row and row[0] is not None else None


def _email_account_scope(
    connection: Connection,
    account_id: int,
) -> int | None:
    row = _first(
        connection,
        select(Market.tenant_id)
        .select_from(OutboundEmailAccount)
        .join(Market, Market.id == OutboundEmailAccount.market_id)
        .where(OutboundEmailAccount.id == account_id),
    )
    return int(row[0]) if row and row[0] is not None else None


def _channel_account_scope(
    connection: Connection,
    account_id: int,
) -> int | None:
    row = _first(
        connection,
        select(ChannelAccount.tenant_id).where(ChannelAccount.id == account_id),
    )
    return int(row[0]) if row and row[0] is not None else None


def derive_job_scope_values(
    connection: Connection,
    job: BackgroundJob,
) -> JobScopeValues:
    payload = _payload(job)
    tenant_candidates: set[int] = set()
    customer_candidates: set[int] = set()
    resource_type: str | None = None
    resource_id: str | None = None

    explicit_tenant_id = _positive_int(payload.get("tenant_id"))
    if explicit_tenant_id is not None:
        row = _first(
            connection,
            select(Tenant.id).where(
                Tenant.id == explicit_tenant_id,
                Tenant.is_active.is_(True),
            ),
        )
        if row:
            tenant_candidates.add(explicit_tenant_id)

    tenant_key_id = _tenant_for_key(connection, payload.get("tenant_key"))
    if tenant_key_id is not None:
        tenant_candidates.add(tenant_key_id)

    ticket_id = _positive_int(payload.get("ticket_id"))
    if ticket_id is not None:
        tenant_id, customer_id = _ticket_scope(connection, ticket_id)
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if customer_id is not None:
            customer_candidates.add(customer_id)
        resource_type, resource_id = "ticket", str(ticket_id)

    conversation_id = _positive_int(payload.get("conversation_id"))
    if conversation_id is not None:
        tenant_id, customer_id, linked_ticket_id = _conversation_scope(
            connection,
            conversation_id,
        )
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if customer_id is not None:
            customer_candidates.add(customer_id)
        if resource_type is None:
            resource_type, resource_id = "conversation", str(conversation_id)
        if ticket_id is None and linked_ticket_id is not None:
            ticket_id = linked_ticket_id

    explicit_customer_id = _positive_int(payload.get("customer_id"))
    if explicit_customer_id is not None:
        customer_tenant_id = _customer_scope(connection, explicit_customer_id)
        if customer_tenant_id is not None:
            tenant_candidates.add(customer_tenant_id)
            customer_candidates.add(explicit_customer_id)
        if resource_type is None:
            resource_type, resource_id = "customer", str(explicit_customer_id)

    account_id = _positive_int(payload.get("account_id"))
    if account_id is not None and job.job_type == "email.mailbox_sync":
        tenant_id = _email_account_scope(connection, account_id)
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if resource_type is None:
            resource_type, resource_id = "outbound_email_account", str(account_id)

    channel_account_id = _positive_int(payload.get("channel_account_id"))
    if channel_account_id is not None:
        tenant_id = _channel_account_scope(connection, channel_account_id)
        if tenant_id is not None:
            tenant_candidates.add(tenant_id)
        if resource_type is None:
            resource_type, resource_id = "channel_account", str(channel_account_id)

    purpose = PURPOSE_BY_JOB_TYPE.get(job.job_type, "unclassified")
    if len(tenant_candidates) == 1:
        tenant_id = next(iter(tenant_candidates))
        valid_customers = {
            customer_id
            for customer_id in customer_candidates
            if _customer_scope(connection, customer_id) == tenant_id
        }
        customer_id = next(iter(valid_customers)) if len(valid_customers) == 1 else None
        return JobScopeValues(
            scope_type="tenant",
            tenant_id=tenant_id,
            customer_id=customer_id,
            purpose=purpose,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    if not tenant_candidates and job.job_type in PLATFORM_JOB_TYPES:
        return JobScopeValues(
            scope_type="platform",
            tenant_id=None,
            customer_id=None,
            purpose=purpose,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    return JobScopeValues(
        scope_type="unresolved",
        tenant_id=None,
        customer_id=None,
        purpose=purpose,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _insert_scope(
    connection: Connection,
    job: BackgroundJob,
) -> None:
    existing = _first(
        connection,
        select(BackgroundJobScope.job_id).where(BackgroundJobScope.job_id == job.id),
    )
    if existing:
        return
    values = derive_job_scope_values(connection, job)
    connection.execute(
        insert(BackgroundJobScope).values(
            job_id=job.id,
            scope_type=values.scope_type,
            tenant_id=values.tenant_id,
            customer_id=values.customer_id,
            purpose=values.purpose,
            resource_type=values.resource_type,
            resource_id=values.resource_id,
            source_schema=SCOPE_SCHEMA,
        )
    )


def _after_background_job_insert(
    _mapper,
    connection: Connection,
    target: BackgroundJob,
) -> None:
    _insert_scope(connection, target)


def install_background_job_scope_events() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(BackgroundJob, "after_insert", _after_background_job_insert)
    _INSTALLED = True


def reconcile_missing_background_job_scopes(
    db: Session,
    *,
    limit: int = 1000,
) -> int:
    rows = (
        db.query(BackgroundJob)
        .outerjoin(
            BackgroundJobScope,
            BackgroundJobScope.job_id == BackgroundJob.id,
        )
        .filter(BackgroundJobScope.job_id.is_(None))
        .order_by(BackgroundJob.id.asc())
        .limit(max(1, min(int(limit), 5000)))
        .all()
    )
    if not rows:
        return 0
    connection = db.connection()
    for job in rows:
        values = derive_job_scope_values(connection, job)
        db.add(
            BackgroundJobScope(
                job_id=job.id,
                scope_type=values.scope_type,
                tenant_id=values.tenant_id,
                customer_id=values.customer_id,
                purpose=values.purpose,
                resource_type=values.resource_type,
                resource_id=values.resource_id,
                source_schema=SCOPE_SCHEMA,
            )
        )
    db.flush()
    return len(rows)


def background_job_ids_for_tenant(
    tenant_id: int,
):
    return select(BackgroundJobScope.job_id).where(
        BackgroundJobScope.scope_type == "tenant",
        BackgroundJobScope.tenant_id == tenant_id,
    )
