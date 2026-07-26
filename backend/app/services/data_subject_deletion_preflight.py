from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..enums import TicketStatus
from ..models import Customer, Ticket
from ..models_case_governance import DataSubjectRequest, LegalHoldRecord
from ..utils.time import utc_now
from .data_lifecycle_service import DataLifecycleError
from .tenant_authority import resolve_actor_tenant_id

ACTIVE_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
}


@dataclass(frozen=True)
class SubjectDeletionPreflight:
    request_id: int
    tenant_id: int
    customer_id: int
    ticket_ids: tuple[int, ...]


def validate_subject_deletion_preflight(
    db: Session,
    *,
    actor,
    request_id: int,
) -> SubjectDeletionPreflight:
    """Validate every reversible deletion blocker before external side effects.

    The canonical anonymization service repeats these invariants immediately
    before database mutation. This preflight exists specifically at the external
    object-storage boundary so a blocked request can never delete evidence first.
    """

    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        raise DataLifecycleError("privacy_requires_tenant_authority", status_code=403)
    request = db.get(DataSubjectRequest, request_id)
    if request is None or request.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_not_found", status_code=404)
    if request.request_type != "delete":
        raise DataLifecycleError("dsar_not_delete_request", status_code=400)
    if request.status == "completed" and request.result_manifest_json:
        return SubjectDeletionPreflight(
            request_id=request.id,
            tenant_id=tenant_id,
            customer_id=request.customer_id,
            ticket_ids=(),
        )
    if request.status not in {"qualified", "processing", "blocked_legal_hold"}:
        raise DataLifecycleError("dsar_not_qualified")
    customer = db.get(Customer, request.customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_customer_authority_conflict")
    tickets = (
        db.query(Ticket)
        .filter(
            Ticket.tenant_id == tenant_id,
            Ticket.customer_id == customer.id,
        )
        .order_by(Ticket.id.asc())
        .all()
    )
    ticket_ids = tuple(int(row.id) for row in tickets)
    if any(row.status in ACTIVE_TICKET_STATUSES for row in tickets):
        request.status = "processing"
        request.blocked_reason = "privacy_active_case_blocks_deletion"
        request.updated_at = utc_now()
        db.flush()
        raise DataLifecycleError("privacy_active_case_blocks_deletion")
    hold = (
        db.query(LegalHoldRecord)
        .filter(
            LegalHoldRecord.tenant_id == tenant_id,
            LegalHoldRecord.status == "active",
            or_(
                LegalHoldRecord.customer_id == customer.id,
                LegalHoldRecord.ticket_id.in_(ticket_ids) if ticket_ids else False,
            ),
        )
        .order_by(LegalHoldRecord.id.asc())
        .first()
    )
    if hold is not None:
        request.status = "blocked_legal_hold"
        request.blocked_reason = "privacy_legal_hold_blocks_deletion"
        request.updated_at = utc_now()
        db.flush()
        raise DataLifecycleError("privacy_legal_hold_blocks_deletion")
    if request.status == "blocked_legal_hold":
        request.status = "qualified"
    request.blocked_reason = None
    request.updated_at = utc_now()
    db.flush()
    return SubjectDeletionPreflight(
        request_id=request.id,
        tenant_id=tenant_id,
        customer_id=customer.id,
        ticket_ids=ticket_ids,
    )
