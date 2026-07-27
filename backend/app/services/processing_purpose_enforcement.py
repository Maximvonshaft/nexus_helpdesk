from __future__ import annotations

from typing import Iterable

from sqlalchemy import event, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ..db import engine
from ..enums import JobStatus, MessageStatus, SourceChannel
from ..models import BackgroundJob, Ticket, TicketOutboundMessage
from ..models_job_scope import BackgroundJobScope
from ..models_privacy_runtime import DataProcessingRestriction
from ..utils.time import utc_now
from .data_subject_action_service import (
    DataProcessingRestricted,
    ensure_data_processing_allowed,
)

PURPOSE_PROVIDER_TOOL_EXECUTION = "provider_tool_execution"
PURPOSE_ANALYTICS = "analytics"
PURPOSE_AUTOMATIC_OUTBOUND = "automatic_outbound"
_CUSTOMER_DELIVERY_CHANNELS = frozenset(
    {
        SourceChannel.email.value,
        SourceChannel.whatsapp.value,
        SourceChannel.telegram.value,
        SourceChannel.sms.value,
    }
)
_INSTALLED = False


def _purpose_is_blocked(row: DataProcessingRestriction, purpose: str) -> bool:
    normalized = str(purpose or "").strip().lower()
    if not normalized:
        return True
    allowed = {
        str(item).strip().lower()
        for item in (row.allowed_purposes_json or [])
    }
    blocked = {
        str(item).strip().lower()
        for item in (row.blocked_purposes_json or [])
    }
    return normalized in blocked or normalized not in allowed


def restricted_customer_ids(
    db: Session,
    *,
    tenant_id: int,
    purpose: str,
) -> tuple[int, ...]:
    rows = (
        db.query(DataProcessingRestriction)
        .filter(
            DataProcessingRestriction.tenant_id == tenant_id,
            DataProcessingRestriction.status == "active",
        )
        .order_by(DataProcessingRestriction.customer_id.asc())
        .all()
    )
    return tuple(
        sorted(
            {
                int(row.customer_id)
                for row in rows
                if _purpose_is_blocked(row, purpose)
            }
        )
    )


def ensure_ticket_processing_allowed(
    db: Session,
    *,
    ticket_id: int | None,
    purpose: str,
    require_ticket: bool = False,
) -> None:
    if ticket_id is None:
        return
    ticket = db.get(Ticket, int(ticket_id))
    if ticket is None:
        if require_ticket:
            raise RuntimeError("processing_guard_ticket_missing")
        return
    ensure_data_processing_allowed(
        db,
        customer_id=ticket.customer_id,
        purpose=purpose,
    )


def ensure_ticket_processing_allowed_fresh(
    *,
    ticket_id: int | None,
    purpose: str,
) -> None:
    """Re-read the latest committed restriction immediately before Provider I/O."""

    if ticket_id is None:
        return
    with Session(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    ) as db:
        ensure_ticket_processing_allowed(
            db,
            ticket_id=ticket_id,
            purpose=purpose,
            require_ticket=True,
        )


def _active_restriction_for_customer(
    connection: Connection,
    *,
    customer_id: int | None,
    purpose: str,
) -> tuple[int, list, list] | None:
    if customer_id is None:
        return None
    rows = connection.execute(
        select(
            DataProcessingRestriction.id,
            DataProcessingRestriction.blocked_purposes_json,
            DataProcessingRestriction.allowed_purposes_json,
        ).where(
            DataProcessingRestriction.customer_id == customer_id,
            DataProcessingRestriction.status == "active",
        )
    ).all()
    normalized = str(purpose or "").strip().lower()
    for restriction_id, blocked_raw, allowed_raw in rows:
        blocked = {
            str(item).strip().lower()
            for item in (blocked_raw or [])
        }
        allowed = {
            str(item).strip().lower()
            for item in (allowed_raw or [])
        }
        if normalized in blocked or normalized not in allowed:
            return (
                int(restriction_id),
                list(blocked_raw or []),
                list(allowed_raw or []),
            )
    return None


def _channel_value(target: TicketOutboundMessage) -> str:
    value = target.channel
    return value.value if hasattr(value, "value") else str(value)


def _automatic_outbound(target: TicketOutboundMessage) -> bool:
    origin = str(target.origin or "").strip().lower()
    provider_status = str(target.provider_status or "").strip().lower()
    if provider_status.startswith("privacy_handoff"):
        return False
    return (
        _channel_value(target) in _CUSTOMER_DELIVERY_CHANNELS
        and target.created_by is None
        and origin not in {"human_agent", "human"}
    )


def _outbound_requires_guard(target: TicketOutboundMessage) -> bool:
    status = (
        target.status.value
        if hasattr(target.status, "value")
        else str(target.status)
    )
    return status in {
        MessageStatus.pending.value,
        MessageStatus.sent.value,
    } and _automatic_outbound(target)


def _guard_outbound_effect(
    _mapper,
    connection: Connection,
    target: TicketOutboundMessage,
) -> None:
    if not _outbound_requires_guard(target):
        return
    row = connection.execute(
        select(Ticket.customer_id).where(Ticket.id == target.ticket_id)
    ).first()
    customer_id = int(row[0]) if row and row[0] is not None else None
    restriction = _active_restriction_for_customer(
        connection,
        customer_id=customer_id,
        purpose=PURPOSE_AUTOMATIC_OUTBOUND,
    )
    if restriction is None:
        return
    restriction_id, _blocked, _allowed = restriction
    raise DataProcessingRestricted(
        customer_id=int(customer_id or 0),
        purpose=PURPOSE_AUTOMATIC_OUTBOUND,
        restriction_id=restriction_id,
    )


def _cancel_pending_restricted_effects(
    _mapper,
    connection: Connection,
    target: DataProcessingRestriction,
) -> None:
    if target.status != "active":
        return
    blocked = {
        str(item).strip().lower()
        for item in (target.blocked_purposes_json or [])
    }
    ticket_ids = select(Ticket.id).where(
        Ticket.tenant_id == target.tenant_id,
        Ticket.customer_id == target.customer_id,
    )
    if PURPOSE_AUTOMATIC_OUTBOUND in blocked:
        connection.execute(
            update(TicketOutboundMessage)
            .where(
                TicketOutboundMessage.ticket_id.in_(ticket_ids),
                TicketOutboundMessage.status == MessageStatus.pending,
                TicketOutboundMessage.created_by.is_(None),
                TicketOutboundMessage.channel.in_(
                    tuple(_CUSTOMER_DELIVERY_CHANNELS)
                ),
            )
            .values(
                status=MessageStatus.dead,
                provider_status="dead:data_processing_restricted",
                failure_code="data_processing_restricted",
                failure_reason=(
                    "Automatic outbound blocked by active processing restriction"
                ),
                error_message=(
                    "Automatic outbound blocked by active processing restriction"
                ),
                next_retry_at=None,
                locked_at=None,
                locked_by=None,
                updated_at=utc_now(),
            )
        )
    blocked_job_purposes = tuple(
        purpose
        for purpose in blocked
        if purpose
        in {
            "automated_ai",
            PURPOSE_PROVIDER_TOOL_EXECUTION,
            PURPOSE_AUTOMATIC_OUTBOUND,
        }
    )
    if blocked_job_purposes:
        job_ids = select(BackgroundJobScope.job_id).where(
            BackgroundJobScope.scope_type == "tenant",
            BackgroundJobScope.tenant_id == target.tenant_id,
            BackgroundJobScope.customer_id == target.customer_id,
            BackgroundJobScope.purpose.in_(blocked_job_purposes),
        )
        connection.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id.in_(job_ids),
                BackgroundJob.status == JobStatus.pending,
            )
            .values(
                status=JobStatus.dead,
                last_error="data_processing_restricted",
                next_run_at=None,
                locked_at=None,
                locked_by=None,
                updated_at=utc_now(),
            )
        )


def install_processing_purpose_events() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(
        TicketOutboundMessage,
        "before_insert",
        _guard_outbound_effect,
    )
    event.listen(
        TicketOutboundMessage,
        "before_update",
        _guard_outbound_effect,
    )
    event.listen(
        DataProcessingRestriction,
        "after_insert",
        _cancel_pending_restricted_effects,
    )
    _INSTALLED = True


def assert_declared_processing_purposes(
    declared: Iterable[str],
) -> None:
    required = {
        "automated_ai",
        PURPOSE_PROVIDER_TOOL_EXECUTION,
        PURPOSE_ANALYTICS,
        PURPOSE_AUTOMATIC_OUTBOUND,
        "model_training",
        "marketing",
    }
    observed = {str(value).strip().lower() for value in declared}
    missing = sorted(required - observed)
    if missing:
        raise RuntimeError(
            "processing_purpose_authority_incomplete:" + ",".join(missing)
        )
