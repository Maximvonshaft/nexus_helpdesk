from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ...models import TicketEvent
from ...models_operations_dispatch import OperationsDispatchOutboxRecord
from .operations_dispatch_outbox import (
    claim_next_operations_dispatch,
    mark_operations_dispatch_failure,
    mark_operations_dispatch_success,
    safe_operations_dispatch_reference,
)


@dataclass(frozen=True)
class OperationsDispatchEnvelope:
    """Safe immutable input to a future governed provider adapter."""

    outbox_id: int
    dispatch_key: str
    tenant_key: str
    country_code: str
    channel_key: str
    routing_rule_id: int
    destination_group_key: str
    destination_group_hash: str
    ticket_id: int | None
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class OperationsDispatchAdapterResult:
    success: bool
    retryable: bool = False
    acknowledgement: Any = None
    external_reference: Any = None
    error_category: str | None = None
    error_summary: str | None = None


class OperationsDispatchAdapter(Protocol):
    def dispatch(self, envelope: OperationsDispatchEnvelope) -> OperationsDispatchAdapterResult:
        ...


class DisabledOperationsDispatchAdapter:
    """Default fail-closed adapter; it never performs external transport."""

    def dispatch(self, envelope: OperationsDispatchEnvelope) -> OperationsDispatchAdapterResult:
        return OperationsDispatchAdapterResult(
            success=False,
            retryable=False,
            error_category="provider_adapter_disabled",
            error_summary="Operations dispatch provider adapter is disabled.",
        )


def process_operations_dispatch_batch(
    db: Session,
    *,
    adapter: OperationsDispatchAdapter,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 120,
) -> int:
    """Claim, commit, invoke, and finalize a bounded dispatch batch.

    The claim transaction is committed before ``adapter.dispatch``. The adapter
    sees only a safe immutable envelope, never an ORM row, customer text, raw
    destination identifier, credential, phone, email, tracking number, or
    provider payload.
    """

    processed = 0
    for _ in range(max(1, min(int(batch_size), 200))):
        record = claim_next_operations_dispatch(
            db,
            lease_owner=worker_id,
            lease_seconds=lease_seconds,
        )
        if record is None:
            db.commit()
            break

        envelope = _envelope(record)
        _audit_timeline(db, record=record, phase="claimed")
        db.commit()

        try:
            result = adapter.dispatch(envelope)
        except Exception as exc:  # adapter boundary must be fail-safe
            result = OperationsDispatchAdapterResult(
                success=False,
                retryable=True,
                error_category=type(exc).__name__,
                error_summary=str(exc),
            )

        if result.success:
            finalized = mark_operations_dispatch_success(
                db,
                record_id=envelope.outbox_id,
                lease_owner=worker_id,
                provider_acknowledgement=result.acknowledgement,
                external_reference=result.external_reference,
            )
            _audit_timeline(db, record=finalized, phase="dispatched")
        else:
            finalized = mark_operations_dispatch_failure(
                db,
                record_id=envelope.outbox_id,
                lease_owner=worker_id,
                retryable=result.retryable,
                error_category=result.error_category or "dispatch_failed",
                error_summary=result.error_summary,
            )
            _audit_timeline(db, record=finalized, phase="failed")

        db.commit()
        processed += 1
    return processed


def _envelope(record: OperationsDispatchOutboxRecord) -> OperationsDispatchEnvelope:
    if record.id is None:
        raise RuntimeError("operations_dispatch_missing_id")
    return OperationsDispatchEnvelope(
        outbox_id=record.id,
        dispatch_key=record.dispatch_key,
        tenant_key=record.tenant_key,
        country_code=record.country_code,
        channel_key=record.channel_key,
        routing_rule_id=record.routing_rule_id,
        destination_group_key=record.destination_group_key,
        destination_group_hash=record.destination_group_hash,
        ticket_id=record.ticket_id,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
    )


def _audit_timeline(db: Session, *, record: OperationsDispatchOutboxRecord, phase: str) -> None:
    if record.ticket_id is None:
        return
    db.add(TicketEvent(
        ticket_id=record.ticket_id,
        event_type="operations_dispatch_outbox",
        actor_type="system",
        actor_user_id=None,
        details_json={
            "phase": phase,
            "dispatch": safe_operations_dispatch_reference(record),
        },
    ))
    db.flush()
