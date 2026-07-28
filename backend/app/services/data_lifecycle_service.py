from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..enums import TicketStatus
from ..models import (
    Customer,
    Ticket,
    TicketAIIntake,
    TicketAttachment,
    TicketComment,
    TicketEvent,
    TicketInboundEmailMessage,
    TicketInternalNote,
    TicketOutboundMessage,
)
from ..models_agent_routing import ConversationControl
from ..models_case_governance import (
    DataLifecycleExecution,
    DataSubjectRequest,
    LegalHoldRecord,
    RetentionPolicyVersion,
)
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .audit_service import log_admin_audit
from .secret_crypto import SecretCryptoService
from .tenant_authority import (
    ensure_resource_tenant,
    resolve_actor_tenant_id,
)
from .whatsapp_privacy_lifecycle import (
    WhatsAppPrivacyLifecycleError,
    collect_whatsapp_subject_export,
    redact_whatsapp_subject_records,
)

ROOT = Path(__file__).resolve().parents[3]
FIELD_AUTHORITY_PATH = ROOT / "config/privacy/data-field-authority.v1.json"
DSAR_DUE_DAYS = 30
MAX_EXPORT_ROWS_PER_COLLECTION = 10_000
ACTIVE_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
}


class DataLifecycleError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class SubjectGraph:
    customer: Customer
    tickets: tuple[Ticket, ...]
    conversation_ids: tuple[int, ...]
    attachment_ids: tuple[int, ...]


@dataclass(frozen=True)
class AnonymizationReceipt:
    customer_id: int
    ticket_count: int
    conversation_count: int
    message_count: int
    related_row_count: int
    receipt_sha256: str


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


def load_data_field_authority() -> dict[str, Any]:
    try:
        payload = json.loads(FIELD_AUTHORITY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataLifecycleError(
            "data_field_authority_unavailable",
            status_code=500,
        ) from exc
    if payload.get("schema") != "nexus.data-field-authority.v1":
        raise DataLifecycleError(
            "data_field_authority_invalid",
            status_code=500,
        )
    resources = payload.get("resources")
    if not isinstance(resources, dict) or not resources:
        raise DataLifecycleError(
            "data_field_authority_empty",
            status_code=500,
        )
    return payload


def _actor_tenant(db: Session, actor) -> int:
    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        raise DataLifecycleError("privacy_requires_tenant_authority", status_code=403)
    return tenant_id


def _customer(
    db: Session,
    *,
    actor,
    customer_id: int,
) -> tuple[int, Customer]:
    tenant_id = _actor_tenant(db, actor)
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise DataLifecycleError("data_subject_not_found", status_code=404)
    ensure_resource_tenant(
        db,
        tenant_id,
        customer,
        resource_kind="Customer",
    )
    return tenant_id, customer


def _fingerprint(value: str) -> str:
    return SecretCryptoService.privacy_identity().fingerprint(value)


def _identity_candidates(customer: Customer) -> set[str]:
    values = {
        str(customer.email or "").strip().lower(),
        str(customer.phone or "").strip(),
        str(customer.external_ref or "").strip(),
    }
    return {value for value in values if value}


def create_data_subject_request(
    db: Session,
    *,
    actor,
    customer_id: int,
    request_key: str,
    request_type: str,
) -> tuple[DataSubjectRequest, bool]:
    tenant_id, customer = _customer(
        db,
        actor=actor,
        customer_id=customer_id,
    )
    key = " ".join(str(request_key or "").strip().split())[:160]
    kind = str(request_type or "").strip().lower()
    if not key:
        raise DataLifecycleError("dsar_request_key_required", status_code=400)
    if kind not in {"access", "export", "delete", "restrict", "correct"}:
        raise DataLifecycleError("dsar_request_type_invalid", status_code=400)
    existing = (
        db.query(DataSubjectRequest)
        .filter(
            DataSubjectRequest.tenant_id == tenant_id,
            DataSubjectRequest.request_key == key,
        )
        .first()
    )
    if existing is not None:
        if existing.customer_id != customer.id or existing.request_type != kind:
            raise DataLifecycleError("dsar_idempotency_conflict")
        return existing, False
    now = utc_now()
    row = DataSubjectRequest(
        tenant_id=tenant_id,
        customer_id=customer.id,
        request_key=key,
        request_type=kind,
        status="identity_pending",
        identity_evidence_hash=None,
        scope_json={
            "schema": "nexus.dsar-scope.v1",
            "customer_id": customer.id,
            "tenant_id": tenant_id,
        },
        result_manifest_json=None,
        result_sha256=None,
        blocked_reason=None,
        received_at=now,
        due_at=now + timedelta(days=DSAR_DUE_DAYS),
        completed_at=None,
        created_by=getattr(actor, "id", None),
        updated_at=now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise DataLifecycleError("dsar_concurrent_conflict") from exc
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.dsar.created",
        target_type="data_subject_request",
        target_id=row.id,
        new_value={
            "request_type": kind,
            "customer_id": customer.id,
            "tenant_id": tenant_id,
            "due_at": row.due_at.isoformat(),
        },
    )
    return row, True


def qualify_data_subject_request(
    db: Session,
    *,
    actor,
    request_id: int,
    identity_evidence: str,
) -> DataSubjectRequest:
    tenant_id = _actor_tenant(db, actor)
    row = db.get(DataSubjectRequest, request_id)
    if row is None or row.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_not_found", status_code=404)
    customer = db.get(Customer, row.customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_customer_authority_conflict")
    normalized = str(identity_evidence or "").strip()
    if not normalized:
        raise DataLifecycleError("dsar_identity_evidence_required", status_code=400)
    candidate = normalized.lower() if "@" in normalized else normalized
    if candidate not in _identity_candidates(customer):
        raise DataLifecycleError("dsar_identity_verification_failed", status_code=403)
    row.identity_evidence_hash = _fingerprint(candidate)
    row.status = "qualified"
    row.blocked_reason = None
    row.updated_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.dsar.qualified",
        target_type="data_subject_request",
        target_id=row.id,
        new_value={
            "identity_verified": True,
            "evidence_hash_prefix": row.identity_evidence_hash[:16],
        },
    )
    db.flush()
    return row


def _subject_graph(
    db: Session,
    *,
    tenant_id: int,
    customer: Customer,
) -> SubjectGraph:
    tickets = tuple(
        db.query(Ticket)
        .filter(
            Ticket.tenant_id == tenant_id,
            Ticket.customer_id == customer.id,
        )
        .order_by(Ticket.id.asc())
        .all()
    )
    ticket_ids = [ticket.id for ticket in tickets]
    conversation_ids: set[int] = set()
    if ticket_ids:
        conversation_ids.update(
            int(value)
            for (value,) in db.query(WebchatConversation.id)
            .filter(WebchatConversation.ticket_id.in_(ticket_ids))
            .all()
        )
    conversation_ids.update(
        int(value)
        for (value,) in db.query(ConversationControl.conversation_id)
        .filter(
            ConversationControl.tenant_key == customer.tenant.tenant_key,
            ConversationControl.customer_id == customer.id,
        )
        .all()
    )
    attachment_ids: tuple[int, ...] = ()
    if ticket_ids:
        attachment_ids = tuple(
            int(value)
            for (value,) in db.query(TicketAttachment.id)
            .filter(TicketAttachment.ticket_id.in_(ticket_ids))
            .order_by(TicketAttachment.id.asc())
            .all()
        )
    return SubjectGraph(
        customer=customer,
        tickets=tickets,
        conversation_ids=tuple(sorted(conversation_ids)),
        attachment_ids=attachment_ids,
    )


def _bounded(rows: list[Any], label: str) -> list[Any]:
    if len(rows) > MAX_EXPORT_ROWS_PER_COLLECTION:
        raise DataLifecycleError(f"dsar_export_{label}_requires_storage_job")
    return rows


def _serialize_ticket(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "description": ticket.description,
        "source": getattr(ticket.source, "value", str(ticket.source)),
        "source_channel": getattr(
            ticket.source_channel,
            "value",
            str(ticket.source_channel),
        ),
        "priority": getattr(ticket.priority, "value", str(ticket.priority)),
        "status": getattr(ticket.status, "value", str(ticket.status)),
        "category": ticket.category,
        "sub_category": ticket.sub_category,
        "tracking_number": ticket.tracking_number,
        "case_type": ticket.case_type,
        "issue_summary": ticket.issue_summary,
        "customer_request": ticket.customer_request,
        "customer_update": ticket.customer_update,
        "resolution_summary": ticket.resolution_summary,
        "destination": ticket.destination,
        "preferred_reply_channel": ticket.preferred_reply_channel,
        "preferred_reply_contact": ticket.preferred_reply_contact,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
    }


def build_data_subject_export(
    db: Session,
    *,
    actor,
    request_id: int,
) -> dict[str, Any]:
    tenant_id = _actor_tenant(db, actor)
    request = db.get(DataSubjectRequest, request_id)
    if request is None or request.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_not_found", status_code=404)
    if request.status not in {"qualified", "processing", "completed"}:
        raise DataLifecycleError("dsar_not_qualified")
    customer = db.get(Customer, request.customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_customer_authority_conflict")
    graph = _subject_graph(db, tenant_id=tenant_id, customer=customer)
    ticket_ids = [ticket.id for ticket in graph.tickets]
    comments = _bounded(
        (
            db.query(TicketComment)
            .filter(TicketComment.ticket_id.in_(ticket_ids))
            .order_by(TicketComment.id.asc())
            .all()
            if ticket_ids
            else []
        ),
        "comments",
    )
    notes = _bounded(
        (
            db.query(TicketInternalNote)
            .filter(TicketInternalNote.ticket_id.in_(ticket_ids))
            .order_by(TicketInternalNote.id.asc())
            .all()
            if ticket_ids
            else []
        ),
        "notes",
    )
    inbound = _bounded(
        (
            db.query(TicketInboundEmailMessage)
            .filter(TicketInboundEmailMessage.ticket_id.in_(ticket_ids))
            .order_by(TicketInboundEmailMessage.id.asc())
            .all()
            if ticket_ids
            else []
        ),
        "inbound_email",
    )
    outbound = _bounded(
        (
            db.query(TicketOutboundMessage)
            .filter(TicketOutboundMessage.ticket_id.in_(ticket_ids))
            .order_by(TicketOutboundMessage.id.asc())
            .all()
            if ticket_ids
            else []
        ),
        "outbound",
    )
    messages = _bounded(
        (
            db.query(WebchatMessage)
            .filter(WebchatMessage.conversation_id.in_(graph.conversation_ids))
            .order_by(WebchatMessage.id.asc())
            .all()
            if graph.conversation_ids
            else []
        ),
        "webchat_messages",
    )
    conversations = _bounded(
        (
            db.query(WebchatConversation)
            .filter(WebchatConversation.id.in_(graph.conversation_ids))
            .order_by(WebchatConversation.id.asc())
            .all()
            if graph.conversation_ids
            else []
        ),
        "conversations",
    )
    try:
        whatsapp_export = collect_whatsapp_subject_export(
            db,
            ticket_ids=ticket_ids,
            conversation_ids=list(graph.conversation_ids),
            max_rows=MAX_EXPORT_ROWS_PER_COLLECTION,
        )
    except WhatsAppPrivacyLifecycleError as exc:
        raise DataLifecycleError(str(exc)) from exc
    payload = {
        "schema": "nexus.data-subject-export.v1",
        "request_id": request.id,
        "generated_at": utc_now().isoformat(),
        "tenant_id": tenant_id,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "external_ref": customer.external_ref,
            "created_at": customer.created_at.isoformat(),
            "updated_at": customer.updated_at.isoformat(),
        },
        "tickets": [_serialize_ticket(ticket) for ticket in graph.tickets],
        "comments": [
            {
                "id": row.id,
                "ticket_id": row.ticket_id,
                "body": row.body,
                "visibility": getattr(row.visibility, "value", str(row.visibility)),
                "created_at": row.created_at.isoformat(),
            }
            for row in comments
        ],
        "internal_notes": [
            {
                "id": row.id,
                "ticket_id": row.ticket_id,
                "body": row.body,
                "created_at": row.created_at.isoformat(),
            }
            for row in notes
        ],
        "inbound_email": [
            {
                "id": row.id,
                "ticket_id": row.ticket_id,
                "from_address": row.from_address,
                "from_name": row.from_name,
                "to_address": row.to_address,
                "cc": row.cc,
                "subject": row.subject,
                "body": row.body,
                "received_at": row.received_at.isoformat(),
            }
            for row in inbound
        ],
        "outbound_messages": [
            {
                "id": row.id,
                "ticket_id": row.ticket_id,
                "channel": getattr(row.channel, "value", str(row.channel)),
                "subject": row.subject,
                "body": row.body,
                "created_at": row.created_at.isoformat(),
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            }
            for row in outbound
        ],
        "conversations": [
            {
                "id": row.id,
                "public_id": row.public_id,
                "ticket_id": row.ticket_id,
                "channel_key": row.channel_key,
                "visitor_name": row.visitor_name,
                "visitor_email": row.visitor_email,
                "visitor_phone": row.visitor_phone,
                "visitor_ref": row.visitor_ref,
                "page_url": row.page_url,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in conversations
        ],
        "webchat_messages": [
            {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "direction": row.direction,
                "body": row.body_text or row.body,
                "message_type": row.message_type,
                "created_at": row.created_at.isoformat(),
            }
            for row in messages
        ],
        **whatsapp_export,
        "attachments": [
            {"id": attachment_id, "external_blob": True}
            for attachment_id in graph.attachment_ids
        ],
    }
    digest = _sha256(payload)
    manifest = {
        "schema": "nexus.data-subject-export-manifest.v1",
        "export_sha256": digest,
        "counts": {
            key: len(value)
            for key, value in payload.items()
            if isinstance(value, list)
        },
        "contains_subject_data": True,
        "persisted_raw_export": False,
    }
    request.result_manifest_json = manifest
    request.result_sha256 = digest
    request.status = (
        "completed" if request.request_type in {"access", "export"} else "processing"
    )
    request.completed_at = utc_now() if request.status == "completed" else None
    request.updated_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.dsar.exported",
        target_type="data_subject_request",
        target_id=request.id,
        new_value=manifest,
    )
    db.flush()
    return payload


def _active_hold(
    db: Session,
    *,
    tenant_id: int,
    customer_id: int,
    ticket_ids: list[int],
) -> LegalHoldRecord | None:
    query = db.query(LegalHoldRecord).filter(
        LegalHoldRecord.tenant_id == tenant_id,
        LegalHoldRecord.status == "active",
        or_(
            LegalHoldRecord.customer_id == customer_id,
            LegalHoldRecord.ticket_id.in_(ticket_ids) if ticket_ids else False,
        ),
    )
    return query.order_by(LegalHoldRecord.id.asc()).first()


def place_legal_hold(
    db: Session,
    *,
    actor,
    customer_id: int | None,
    ticket_id: int | None,
    reason_code: str,
    note: str | None = None,
) -> LegalHoldRecord:
    tenant_id = _actor_tenant(db, actor)
    if customer_id is None and ticket_id is None:
        raise DataLifecycleError("legal_hold_subject_required", status_code=400)
    if customer_id is not None:
        customer = db.get(Customer, customer_id)
        if customer is None or customer.tenant_id != tenant_id:
            raise DataLifecycleError("legal_hold_subject_not_found", status_code=404)
    if ticket_id is not None:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None or ticket.tenant_id != tenant_id:
            raise DataLifecycleError("legal_hold_subject_not_found", status_code=404)
        if customer_id is not None and ticket.customer_id != customer_id:
            raise DataLifecycleError("legal_hold_subject_conflict")
    reason = " ".join(str(reason_code or "").strip().split())[:120]
    if not reason:
        raise DataLifecycleError("legal_hold_reason_required", status_code=400)
    row = LegalHoldRecord(
        tenant_id=tenant_id,
        customer_id=customer_id,
        ticket_id=ticket_id,
        reason_code=reason,
        note=(" ".join(str(note or "").strip().split())[:1000] or None),
        status="active",
        placed_by=getattr(actor, "id", None),
        released_by=None,
        placed_at=utc_now(),
        released_at=None,
    )
    db.add(row)
    db.flush()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.legal_hold.placed",
        target_type="legal_hold",
        target_id=row.id,
        new_value={
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "ticket_id": ticket_id,
            "reason_code": reason,
        },
    )
    return row


def release_legal_hold(
    db: Session,
    *,
    actor,
    hold_id: int,
) -> LegalHoldRecord:
    tenant_id = _actor_tenant(db, actor)
    row = db.get(LegalHoldRecord, hold_id)
    if row is None or row.tenant_id != tenant_id:
        raise DataLifecycleError("legal_hold_not_found", status_code=404)
    if row.status == "released":
        return row
    row.status = "released"
    row.released_by = getattr(actor, "id", None)
    row.released_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.legal_hold.released",
        target_type="legal_hold",
        target_id=row.id,
        new_value={"status": "released"},
    )
    db.flush()
    return row


def _anonymized(value: str, *, namespace: str, record_id: int) -> str:
    digest = hashlib.sha256(
        f"{namespace}:{record_id}:{value}".encode("utf-8")
    ).hexdigest()[:20]
    return f"erased-{namespace}-{digest}"


def _anonymize_subject_graph(
    db: Session,
    *,
    tenant_id: int,
    graph: SubjectGraph,
    actor_id: int | None,
) -> AnonymizationReceipt:
    ticket_ids = [ticket.id for ticket in graph.tickets]
    if graph.attachment_ids:
        raise DataLifecycleError("privacy_attachment_blob_receipt_required")
    active = [
        ticket.id
        for ticket in graph.tickets
        if ticket.status in ACTIVE_TICKET_STATUSES
    ]
    if active:
        raise DataLifecycleError("privacy_active_case_blocks_deletion")
    hold = _active_hold(
        db,
        tenant_id=tenant_id,
        customer_id=graph.customer.id,
        ticket_ids=ticket_ids,
    )
    if hold is not None:
        raise DataLifecycleError("privacy_legal_hold_blocks_deletion")

    related = 0
    message_count = 0
    customer = graph.customer
    customer.name = _anonymized(
        customer.name,
        namespace="customer",
        record_id=customer.id,
    )
    customer.email = None
    customer.email_normalized = None
    customer.phone = None
    customer.phone_normalized = None
    customer.external_ref = None
    customer.updated_at = utc_now()

    for ticket in graph.tickets:
        ticket.title = "Erased customer case"
        ticket.description = "[redacted by privacy request]"
        ticket.tracking_number = None
        ticket.ai_summary = None
        ticket.issue_summary = None
        ticket.customer_request = None
        ticket.source_chat_id = None
        ticket.source_dedupe_key = None
        ticket.required_action = None
        ticket.missing_fields = None
        ticket.last_customer_message = None
        ticket.customer_update = None
        ticket.resolution_summary = None
        ticket.last_human_update = None
        ticket.last_ai_update = None
        ticket.requested_time = None
        ticket.destination = None
        ticket.preferred_reply_channel = None
        ticket.preferred_reply_contact = None
        ticket.updated_at = utc_now()

    if ticket_ids:
        comments = (
            db.query(TicketComment)
            .filter(TicketComment.ticket_id.in_(ticket_ids))
            .all()
        )
        for row in comments:
            row.body = "[redacted by privacy request]"
        related += len(comments)
        notes = (
            db.query(TicketInternalNote)
            .filter(TicketInternalNote.ticket_id.in_(ticket_ids))
            .all()
        )
        for row in notes:
            row.body = "[redacted by privacy request]"
        related += len(notes)
        events = (
            db.query(TicketEvent)
            .filter(TicketEvent.ticket_id.in_(ticket_ids))
            .all()
        )
        for row in events:
            row.old_value = None
            row.new_value = None
            row.note = "[redacted by privacy request]"
            row.payload_json = json.dumps(
                {
                    "schema": "nexus.privacy-redaction.v1",
                    "redacted": True,
                },
                separators=(",", ":"),
            )
        related += len(events)
        outbound = (
            db.query(TicketOutboundMessage)
            .filter(TicketOutboundMessage.ticket_id.in_(ticket_ids))
            .all()
        )
        for row in outbound:
            row.subject = None
            row.body = "[redacted by privacy request]"
            row.runtime_contract_payload_json = None
            row.error_message = None
            row.mailbox_thread_id = None
            row.mailbox_message_id = None
            row.mailbox_references = None
            row.failure_reason = None
            row.delivery_detail = None
            row.delivery_payload_json = None
        related += len(outbound)
        inbound = (
            db.query(TicketInboundEmailMessage)
            .filter(TicketInboundEmailMessage.ticket_id.in_(ticket_ids))
            .all()
        )
        for row in inbound:
            row.from_address = (
                _anonymized(row.from_address, namespace="email", record_id=row.id)
                + "@invalid"
            )
            row.from_name = None
            row.to_address = None
            row.cc = None
            row.subject = None
            row.body = "[redacted by privacy request]"
            row.body_preview = None
            row.mailbox_thread_id = _anonymized(
                row.mailbox_thread_id,
                namespace="thread",
                record_id=row.id,
            )
            row.mailbox_message_id = None
            row.mailbox_references = None
            row.in_reply_to = None
        related += len(inbound)
        intakes = (
            db.query(TicketAIIntake)
            .filter(TicketAIIntake.ticket_id.in_(ticket_ids))
            .all()
        )
        for row in intakes:
            row.summary = "[redacted by privacy request]"
            row.missing_fields_json = None
            row.recommended_action = None
            row.suggested_reply = None
            row.raw_payload_json = None
            row.human_override_reason = None
        related += len(intakes)

    try:
        related += redact_whatsapp_subject_records(
            db,
            ticket_ids=ticket_ids,
            conversation_ids=list(graph.conversation_ids),
            anonymize=_anonymized,
        )
    except WhatsAppPrivacyLifecycleError as exc:
        raise DataLifecycleError(str(exc)) from exc

    if graph.conversation_ids:
        conversations = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.id.in_(graph.conversation_ids))
            .all()
        )
        for row in conversations:
            row.visitor_name = _anonymized(
                row.visitor_name or "visitor",
                namespace="visitor",
                record_id=row.id,
            )
            row.visitor_email = None
            row.visitor_phone = None
            row.visitor_ref = None
            row.page_url = None
            row.user_agent = None
            row.last_tracking_number = None
            row.ai_suspended_reason = None
            row.last_handoff_reason = None
            row.updated_at = utc_now()
        related += len(conversations)
        messages = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.conversation_id.in_(graph.conversation_ids))
            .all()
        )
        for row in messages:
            row.body = "[redacted by privacy request]"
            row.body_text = None
            row.payload_json = None
            row.metadata_json = None
            row.author_label = None
            row.safety_reasons_json = None
        message_count = len(messages)
        related += message_count
        turns = (
            db.query(WebchatAITurn)
            .filter(WebchatAITurn.conversation_id.in_(graph.conversation_ids))
            .all()
        )
        for row in turns:
            row.status_reason = None
            row.runtime_trace_json = None
            row.fallback_reason = None
            row.fact_gate_reason = None
        related += len(turns)

    receipt_without_hash = {
        "schema": "nexus.subject-anonymization-receipt.v1",
        "tenant_id": tenant_id,
        "customer_id": customer.id,
        "ticket_ids": ticket_ids,
        "conversation_ids": list(graph.conversation_ids),
        "ticket_count": len(ticket_ids),
        "conversation_count": len(graph.conversation_ids),
        "message_count": message_count,
        "related_row_count": related,
        "actor_id": actor_id,
        "completed_at": utc_now().isoformat(),
        "raw_values_persisted": False,
    }
    digest = _sha256(receipt_without_hash)
    db.flush()
    return AnonymizationReceipt(
        customer_id=customer.id,
        ticket_count=len(ticket_ids),
        conversation_count=len(graph.conversation_ids),
        message_count=message_count,
        related_row_count=related,
        receipt_sha256=digest,
    )


def execute_data_subject_deletion(
    db: Session,
    *,
    actor,
    request_id: int,
) -> AnonymizationReceipt:
    tenant_id = _actor_tenant(db, actor)
    request = db.get(DataSubjectRequest, request_id)
    if request is None or request.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_not_found", status_code=404)
    if request.request_type != "delete":
        raise DataLifecycleError("dsar_not_delete_request", status_code=400)
    if request.status == "completed" and request.result_manifest_json:
        manifest = request.result_manifest_json
        return AnonymizationReceipt(
            customer_id=request.customer_id,
            ticket_count=int(manifest.get("ticket_count") or 0),
            conversation_count=int(manifest.get("conversation_count") or 0),
            message_count=int(manifest.get("message_count") or 0),
            related_row_count=int(manifest.get("related_row_count") or 0),
            receipt_sha256=str(request.result_sha256 or ""),
        )
    if request.status not in {"qualified", "processing"}:
        raise DataLifecycleError("dsar_not_qualified")
    customer = db.get(Customer, request.customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_customer_authority_conflict")
    graph = _subject_graph(db, tenant_id=tenant_id, customer=customer)
    try:
        receipt = _anonymize_subject_graph(
            db,
            tenant_id=tenant_id,
            graph=graph,
            actor_id=getattr(actor, "id", None),
        )
    except DataLifecycleError as exc:
        request.status = (
            "blocked_legal_hold"
            if exc.code == "privacy_legal_hold_blocks_deletion"
            else "processing"
        )
        request.blocked_reason = exc.code
        request.updated_at = utc_now()
        db.flush()
        raise
    manifest = {
        "schema": "nexus.subject-anonymization-manifest.v1",
        "ticket_count": receipt.ticket_count,
        "conversation_count": receipt.conversation_count,
        "message_count": receipt.message_count,
        "related_row_count": receipt.related_row_count,
        "raw_values_persisted": False,
    }
    request.status = "completed"
    request.blocked_reason = None
    request.result_manifest_json = manifest
    request.result_sha256 = receipt.receipt_sha256
    request.completed_at = utc_now()
    request.updated_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.dsar.deleted",
        target_type="data_subject_request",
        target_id=request.id,
        new_value={
            **manifest,
            "receipt_sha256": receipt.receipt_sha256,
        },
    )
    db.flush()
    return receipt


def create_retention_policy(
    db: Session,
    *,
    actor,
    resource_type: str,
    retention_days: int,
    legal_basis: str,
    action: str = "anonymize",
) -> RetentionPolicyVersion:
    authority = load_data_field_authority()
    tenant_id = _actor_tenant(db, actor)
    resource = str(resource_type or "").strip().lower()
    if resource not in set(authority.get("retention_resources") or []):
        raise DataLifecycleError("retention_resource_invalid", status_code=400)
    if retention_days < 0:
        raise DataLifecycleError("retention_days_invalid", status_code=400)
    if action != "anonymize":
        raise DataLifecycleError("retention_action_not_supported", status_code=400)
    latest = (
        db.query(RetentionPolicyVersion)
        .filter(
            RetentionPolicyVersion.tenant_id == tenant_id,
            RetentionPolicyVersion.resource_type == resource,
        )
        .order_by(RetentionPolicyVersion.version.desc())
        .first()
    )
    version = int(latest.version if latest else 0) + 1
    if latest is not None and latest.status == "approved":
        latest.status = "retired"
        latest.effective_to = utc_now()
    row = RetentionPolicyVersion(
        tenant_id=tenant_id,
        is_global_template=False,
        resource_type=resource,
        version=version,
        retention_days=retention_days,
        legal_basis=" ".join(str(legal_basis or "").strip().split())[:160],
        action=action,
        status="approved",
        effective_from=utc_now(),
        effective_to=None,
        approved_by=getattr(actor, "id", None),
        created_at=utc_now(),
    )
    if not row.legal_basis:
        raise DataLifecycleError("retention_legal_basis_required", status_code=400)
    db.add(row)
    db.flush()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.retention_policy.approved",
        target_type="retention_policy_version",
        target_id=row.id,
        new_value={
            "resource_type": resource,
            "version": version,
            "retention_days": retention_days,
            "action": action,
        },
    )
    return row


def plan_retention_execution(
    db: Session,
    *,
    actor,
    policy_id: int,
    execution_key: str,
) -> DataLifecycleExecution:
    tenant_id = _actor_tenant(db, actor)
    policy = db.get(RetentionPolicyVersion, policy_id)
    if policy is None or policy.tenant_id != tenant_id or policy.status != "approved":
        raise DataLifecycleError("retention_policy_not_found", status_code=404)
    if policy.resource_type != "customer_profile":
        raise DataLifecycleError("retention_resource_apply_not_implemented")
    key = " ".join(str(execution_key or "").strip().split())[:160]
    if not key:
        raise DataLifecycleError("retention_execution_key_required", status_code=400)
    existing = (
        db.query(DataLifecycleExecution)
        .filter(
            DataLifecycleExecution.tenant_id == tenant_id,
            DataLifecycleExecution.execution_key == key,
        )
        .first()
    )
    if existing is not None:
        if existing.policy_version_id != policy.id:
            raise DataLifecycleError("retention_execution_idempotency_conflict")
        return existing
    cutoff = utc_now() - timedelta(days=policy.retention_days)
    candidates = (
        db.query(Customer)
        .filter(
            Customer.tenant_id == tenant_id,
            Customer.updated_at <= cutoff,
        )
        .order_by(Customer.id.asc())
        .all()
    )
    eligible: list[int] = []
    held = 0
    blocked_active = 0
    blocked_attachments = 0
    for customer in candidates:
        graph = _subject_graph(db, tenant_id=tenant_id, customer=customer)
        ticket_ids = [ticket.id for ticket in graph.tickets]
        if _active_hold(
            db,
            tenant_id=tenant_id,
            customer_id=customer.id,
            ticket_ids=ticket_ids,
        ) is not None:
            held += 1
            continue
        if any(ticket.status in ACTIVE_TICKET_STATUSES for ticket in graph.tickets):
            blocked_active += 1
            continue
        if graph.attachment_ids:
            blocked_attachments += 1
            continue
        eligible.append(customer.id)
    receipt = {
        "schema": "nexus.retention-dry-run.v1",
        "policy_id": policy.id,
        "resource_type": policy.resource_type,
        "cutoff_at": cutoff.isoformat(),
        "candidate_ids": eligible,
        "scanned_count": len(candidates),
        "eligible_count": len(eligible),
        "held_count": held,
        "blocked_active_count": blocked_active,
        "blocked_attachment_count": blocked_attachments,
        "contains_subject_values": False,
    }
    digest = _sha256(receipt)
    row = DataLifecycleExecution(
        tenant_id=tenant_id,
        policy_version_id=policy.id,
        execution_key=key,
        status="dry_run",
        cutoff_at=cutoff,
        scanned_count=len(candidates),
        affected_count=0,
        held_count=held,
        receipt_json=receipt,
        receipt_sha256=digest,
        created_by=getattr(actor, "id", None),
        created_at=utc_now(),
        completed_at=None,
    )
    db.add(row)
    db.flush()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.retention.dry_run",
        target_type="data_lifecycle_execution",
        target_id=row.id,
        new_value={
            "receipt_sha256": digest,
            "scanned_count": row.scanned_count,
            "eligible_count": len(eligible),
            "held_count": held,
        },
    )
    return row


def apply_retention_execution(
    db: Session,
    *,
    actor,
    execution_id: int,
) -> DataLifecycleExecution:
    tenant_id = _actor_tenant(db, actor)
    execution = db.get(DataLifecycleExecution, execution_id)
    if execution is None or execution.tenant_id != tenant_id:
        raise DataLifecycleError("retention_execution_not_found", status_code=404)
    if execution.status == "applied":
        return execution
    if execution.status != "dry_run":
        raise DataLifecycleError("retention_execution_not_ready")
    candidate_ids = [
        int(value)
        for value in (execution.receipt_json or {}).get("candidate_ids", [])
    ]
    affected = 0
    held = execution.held_count
    receipts: list[str] = []
    for customer_id in candidate_ids:
        customer = db.get(Customer, customer_id)
        if customer is None or customer.tenant_id != tenant_id:
            continue
        graph = _subject_graph(db, tenant_id=tenant_id, customer=customer)
        try:
            receipt = _anonymize_subject_graph(
                db,
                tenant_id=tenant_id,
                graph=graph,
                actor_id=getattr(actor, "id", None),
            )
        except DataLifecycleError as exc:
            if exc.code == "privacy_legal_hold_blocks_deletion":
                held += 1
                continue
            raise
        affected += 1
        receipts.append(receipt.receipt_sha256)
    applied_receipt = {
        "schema": "nexus.retention-application.v1",
        "dry_run_sha256": execution.receipt_sha256,
        "affected_count": affected,
        "held_count": held,
        "subject_receipt_sha256": receipts,
        "contains_subject_values": False,
        "applied_at": utc_now().isoformat(),
    }
    execution.status = "applied"
    execution.affected_count = affected
    execution.held_count = held
    execution.receipt_json = applied_receipt
    execution.receipt_sha256 = _sha256(applied_receipt)
    execution.completed_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.retention.applied",
        target_type="data_lifecycle_execution",
        target_id=execution.id,
        new_value={
            "receipt_sha256": execution.receipt_sha256,
            "affected_count": affected,
            "held_count": held,
        },
    )
    db.flush()
    return execution
