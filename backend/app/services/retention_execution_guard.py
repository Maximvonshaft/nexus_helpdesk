from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..enums import TicketStatus
from ..models import Customer, Ticket, TicketAttachment
from ..models_case_governance import DataLifecycleExecution, RetentionPolicyVersion
from ..utils.time import ensure_utc
from .data_lifecycle_service import (
    DataLifecycleError,
    apply_retention_execution as _apply_retention_execution,
)
from .tenant_authority import resolve_actor_tenant_id

MAX_RETENTION_CANDIDATES = 10_000
ACTIVE_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_cutoff(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DataLifecycleError("retention_dry_run_cutoff_invalid") from exc
    normalized = ensure_utc(parsed)
    if normalized is None:
        raise DataLifecycleError("retention_dry_run_cutoff_invalid")
    return normalized


def _candidate_ids(receipt: dict[str, Any]) -> list[int]:
    raw = receipt.get("candidate_ids")
    if not isinstance(raw, list):
        raise DataLifecycleError("retention_dry_run_candidates_invalid")
    try:
        values = [int(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise DataLifecycleError("retention_dry_run_candidates_invalid") from exc
    if any(value <= 0 for value in values):
        raise DataLifecycleError("retention_dry_run_candidates_invalid")
    if len(values) != len(set(values)):
        raise DataLifecycleError("retention_dry_run_candidates_duplicate")
    if len(values) > MAX_RETENTION_CANDIDATES:
        raise DataLifecycleError("retention_dry_run_candidates_unbounded")
    if int(receipt.get("eligible_count") or 0) != len(values):
        raise DataLifecycleError("retention_dry_run_candidate_count_mismatch")
    return values


def _lock_customers(
    db: Session,
    *,
    tenant_id: int,
    candidate_ids: list[int],
) -> list[Customer]:
    if not candidate_ids:
        return []
    query = (
        db.query(Customer)
        .filter(
            Customer.tenant_id == tenant_id,
            Customer.id.in_(candidate_ids),
        )
        .order_by(Customer.id.asc())
    )
    if db.bind is not None and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    rows = query.all()
    if [row.id for row in rows] != sorted(candidate_ids):
        raise DataLifecycleError("retention_candidate_identity_drift")
    return rows


def preflight_retention_execution(
    db: Session,
    *,
    actor,
    execution_id: int,
) -> DataLifecycleExecution:
    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        raise DataLifecycleError("privacy_requires_tenant_authority", status_code=403)
    execution = db.get(DataLifecycleExecution, execution_id)
    if execution is None or execution.tenant_id != tenant_id:
        raise DataLifecycleError("retention_execution_not_found", status_code=404)
    if execution.status == "applied":
        return execution
    if execution.status != "dry_run":
        raise DataLifecycleError("retention_execution_not_ready")

    receipt = execution.receipt_json or {}
    if receipt.get("schema") != "nexus.retention-dry-run.v1":
        raise DataLifecycleError("retention_dry_run_schema_invalid")
    if not execution.receipt_sha256 or _sha256(receipt) != execution.receipt_sha256:
        raise DataLifecycleError("retention_dry_run_receipt_hash_mismatch")

    policy = db.get(RetentionPolicyVersion, execution.policy_version_id)
    if (
        policy is None
        or policy.tenant_id != tenant_id
        or policy.status != "approved"
        or policy.resource_type != "customer_profile"
    ):
        raise DataLifecycleError("retention_policy_changed_since_dry_run")
    if int(receipt.get("policy_id") or 0) != policy.id:
        raise DataLifecycleError("retention_dry_run_policy_mismatch")
    if receipt.get("resource_type") != policy.resource_type:
        raise DataLifecycleError("retention_dry_run_resource_mismatch")

    cutoff = _parse_cutoff(receipt.get("cutoff_at"))
    if ensure_utc(execution.cutoff_at) != cutoff:
        raise DataLifecycleError("retention_dry_run_cutoff_mismatch")
    candidate_ids = _candidate_ids(receipt)
    customers = _lock_customers(
        db,
        tenant_id=tenant_id,
        candidate_ids=candidate_ids,
    )

    for customer in customers:
        updated_at = ensure_utc(customer.updated_at)
        if updated_at is None or updated_at > cutoff:
            raise DataLifecycleError("retention_candidate_data_drift")
        active_case = (
            db.query(Ticket.id)
            .filter(
                Ticket.tenant_id == tenant_id,
                Ticket.customer_id == customer.id,
                Ticket.status.in_(ACTIVE_TICKET_STATUSES),
            )
            .first()
        )
        if active_case is not None:
            raise DataLifecycleError("retention_candidate_active_case_drift")
        attachment = (
            db.query(TicketAttachment.id)
            .join(Ticket, Ticket.id == TicketAttachment.ticket_id)
            .filter(
                Ticket.tenant_id == tenant_id,
                Ticket.customer_id == customer.id,
            )
            .first()
        )
        if attachment is not None:
            raise DataLifecycleError("retention_candidate_attachment_drift")
    return execution


def apply_retention_execution(
    db: Session,
    *,
    actor,
    execution_id: int,
) -> DataLifecycleExecution:
    execution = preflight_retention_execution(
        db,
        actor=actor,
        execution_id=execution_id,
    )
    if execution.status == "applied":
        return execution
    return _apply_retention_execution(
        db,
        actor=actor,
        execution_id=execution_id,
    )
