from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ...models_operations_dispatch import OperationsDispatchOutboxRecord
from .operations_dispatch_outbox import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_BACKOFF_MAX_SECONDS,
    DEFAULT_LEASE_SECONDS,
    claim_next_operations_dispatch,
    mark_operations_dispatch_failed,
    mark_operations_dispatch_succeeded,
)


@dataclass(frozen=True)
class OperationsDispatchRequest:
    """Adapter-neutral, non-customer-visible dispatch request.

    `dispatch_key` is the required provider idempotency key. This contract does
    not contain a raw provider group ID or a message body; a future approved
    adapter must resolve provider-specific data through a separately governed
    contract using the routing rule and safe destination references.
    """

    outbox_id: int
    dispatch_key: str
    tenant_key: str
    country_code: str
    channel_key: str
    routing_rule_id: int
    destination_group_key: str
    destination_group_hash: str
    attempt_count: int


@dataclass(frozen=True)
class OperationsDispatchAdapterResult:
    dispatched: bool
    retryable: bool = False
    provider_acknowledgement: Any = None
    external_reference: str | None = None
    error_category: str | None = None
    error_summary: Any = None


class OperationsDispatchAdapter(Protocol):
    def dispatch(self, request: OperationsDispatchRequest) -> OperationsDispatchAdapterResult:
        ...


def process_next_operations_dispatch(
    db: Session,
    *,
    adapter: OperationsDispatchAdapter,
    lease_owner: str,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    tenant_key: str | None = None,
    country_code: str | None = None,
    channel_key: str | None = None,
    backoff_base_seconds: int = DEFAULT_BACKOFF_BASE_SECONDS,
    backoff_max_seconds: int = DEFAULT_BACKOFF_MAX_SECONDS,
) -> OperationsDispatchOutboxRecord | None:
    """Process one record without coupling the outbox to a provider or sidecar.

    The lease is committed before the adapter is invoked so another worker can
    recover it after expiry if this process crashes. The future provider adapter
    must honor `request.dispatch_key` as an idempotency key to close the
    send-success/ack-persist crash window.
    """

    record = claim_next_operations_dispatch(
        db,
        lease_owner=lease_owner,
        now=now,
        lease_seconds=lease_seconds,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
    )
    if record is None:
        db.commit()
        return None
    db.commit()

    request = OperationsDispatchRequest(
        outbox_id=record.id,
        dispatch_key=record.dispatch_key,
        tenant_key=record.tenant_key,
        country_code=record.country_code,
        channel_key=record.channel_key,
        routing_rule_id=record.routing_rule_id,
        destination_group_key=record.destination_group_key,
        destination_group_hash=record.destination_group_hash,
        attempt_count=record.attempt_count,
    )

    try:
        result = adapter.dispatch(request)
    except Exception as exc:  # adapter failures become bounded retry state
        updated = mark_operations_dispatch_failed(
            db,
            record_id=record.id,
            lease_owner=lease_owner,
            retryable=True,
            error_category=type(exc).__name__,
            error_summary=str(exc),
            now=now,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
        )
        db.commit()
        return updated

    if not isinstance(result, OperationsDispatchAdapterResult):
        updated = mark_operations_dispatch_failed(
            db,
            record_id=record.id,
            lease_owner=lease_owner,
            retryable=False,
            error_category="invalid_adapter_result",
            error_summary="operations dispatch adapter returned an unsupported result",
            now=now,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
        )
        db.commit()
        return updated

    if result.dispatched:
        updated = mark_operations_dispatch_succeeded(
            db,
            record_id=record.id,
            lease_owner=lease_owner,
            provider_acknowledgement=result.provider_acknowledgement,
            external_reference=result.external_reference,
            now=now,
        )
    else:
        updated = mark_operations_dispatch_failed(
            db,
            record_id=record.id,
            lease_owner=lease_owner,
            retryable=result.retryable,
            error_category=result.error_category,
            error_summary=result.error_summary,
            provider_acknowledgement=result.provider_acknowledgement,
            now=now,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
        )
    db.commit()
    return updated
