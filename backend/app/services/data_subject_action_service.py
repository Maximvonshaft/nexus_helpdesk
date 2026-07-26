from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Customer, Ticket
from ..models_agent_routing import ConversationControl
from ..models_case_governance import DataSubjectRequest
from ..models_privacy_runtime import DataProcessingRestriction
from ..utils.normalize import normalize_email, normalize_phone
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .audit_service import log_admin_audit
from .data_lifecycle_service import DataLifecycleError
from .tenant_authority import resolve_actor_tenant_id

ALLOWED_PURPOSES_WHEN_RESTRICTED = frozenset(
    {
        "human_support",
        "legal_obligation",
        "security",
        "fraud_prevention",
        "dsar",
        "retention",
    }
)
DEFAULT_BLOCKED_PURPOSES = frozenset(
    {
        "automated_ai",
        "provider_tool_execution",
        "analytics",
        "model_training",
        "marketing",
        "automatic_outbound",
    }
)


class DataProcessingRestricted(RuntimeError):
    def __init__(self, *, customer_id: int, purpose: str, restriction_id: int) -> None:
        self.customer_id = customer_id
        self.purpose = purpose
        self.restriction_id = restriction_id
        super().__init__("data_processing_restricted")


@dataclass(frozen=True)
class CorrectionResult:
    request: DataSubjectRequest
    customer: Customer
    fields: tuple[str, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _request(
    db: Session,
    *,
    actor,
    request_id: int,
    request_type: str,
) -> tuple[int, DataSubjectRequest, Customer]:
    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        raise DataLifecycleError("privacy_requires_tenant_authority", status_code=403)
    request = db.get(DataSubjectRequest, request_id)
    if request is None or request.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_not_found", status_code=404)
    if request.request_type != request_type:
        raise DataLifecycleError(f"dsar_not_{request_type}_request", status_code=400)
    if request.status not in {"qualified", "processing", "completed"}:
        raise DataLifecycleError("dsar_not_qualified")
    customer = db.get(Customer, request.customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise DataLifecycleError("dsar_customer_authority_conflict")
    return tenant_id, request, customer


def _correction_payload(
    *,
    name: str | None,
    email: str | None,
    phone: str | None,
    external_ref: str | None,
) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    if name is not None:
        normalized = " ".join(str(name).strip().split())
        if not normalized:
            raise DataLifecycleError("dsar_correction_name_invalid", status_code=400)
        values["name"] = normalized[:160]
    if email is not None:
        normalized_email = normalize_email(email)
        if not normalized_email:
            raise DataLifecycleError("dsar_correction_email_invalid", status_code=400)
        values["email"] = normalized_email
    if phone is not None:
        normalized_phone = normalize_phone(phone)
        if not normalized_phone:
            raise DataLifecycleError("dsar_correction_phone_invalid", status_code=400)
        values["phone"] = normalized_phone
    if external_ref is not None:
        normalized_ref = " ".join(str(external_ref).strip().split())
        if not normalized_ref:
            raise DataLifecycleError(
                "dsar_correction_external_ref_invalid",
                status_code=400,
            )
        values["external_ref"] = normalized_ref[:120]
    if not values:
        raise DataLifecycleError("dsar_correction_fields_required", status_code=400)
    return values


def execute_data_subject_correction(
    db: Session,
    *,
    actor,
    request_id: int,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    external_ref: str | None = None,
) -> CorrectionResult:
    tenant_id, request, customer = _request(
        db,
        actor=actor,
        request_id=request_id,
        request_type="correct",
    )
    values = _correction_payload(
        name=name,
        email=email,
        phone=phone,
        external_ref=external_ref,
    )
    payload_hash = _digest(values)
    existing = request.result_manifest_json or {}
    if request.status == "completed":
        if (
            existing.get("schema") != "nexus.subject-correction-manifest.v1"
            or existing.get("correction_sha256") != payload_hash
        ):
            raise DataLifecycleError("dsar_correction_idempotency_conflict")
        return CorrectionResult(
            request=request,
            customer=customer,
            fields=tuple(existing.get("fields") or ()),
        )

    if "email" in values:
        conflict = (
            db.query(Customer.id)
            .filter(
                Customer.tenant_id == tenant_id,
                Customer.id != customer.id,
                Customer.email_normalized == values["email"],
            )
            .first()
        )
        if conflict:
            raise DataLifecycleError("dsar_correction_email_conflict")
    if "phone" in values:
        conflict = (
            db.query(Customer.id)
            .filter(
                Customer.tenant_id == tenant_id,
                Customer.id != customer.id,
                Customer.phone_normalized == values["phone"],
            )
            .first()
        )
        if conflict:
            raise DataLifecycleError("dsar_correction_phone_conflict")
    if "external_ref" in values:
        conflict = (
            db.query(Customer.id)
            .filter(
                Customer.tenant_id == tenant_id,
                Customer.id != customer.id,
                Customer.external_ref == values["external_ref"],
            )
            .first()
        )
        if conflict:
            raise DataLifecycleError("dsar_correction_external_ref_conflict")

    for field, value in values.items():
        setattr(customer, field, value)
    if "email" in values:
        customer.email_normalized = values["email"]
    if "phone" in values:
        customer.phone_normalized = values["phone"]
    customer.updated_at = utc_now()
    manifest = {
        "schema": "nexus.subject-correction-manifest.v1",
        "fields": sorted(values),
        "correction_sha256": payload_hash,
        "raw_values_persisted": False,
    }
    request.status = "completed"
    request.blocked_reason = None
    request.result_manifest_json = manifest
    request.result_sha256 = _digest(manifest)
    request.completed_at = utc_now()
    request.updated_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.dsar.corrected",
        target_type="data_subject_request",
        target_id=request.id,
        new_value={
            "customer_id": customer.id,
            "fields": sorted(values),
            "correction_sha256": payload_hash,
        },
    )
    db.flush()
    return CorrectionResult(
        request=request,
        customer=customer,
        fields=tuple(sorted(values)),
    )


def _subject_open_conversations(
    db: Session,
    *,
    tenant_id: int,
    customer_id: int,
) -> list[WebchatConversation]:
    ticket_ids = [
        int(ticket_id)
        for (ticket_id,) in db.query(Ticket.id)
        .filter(
            Ticket.tenant_id == tenant_id,
            Ticket.customer_id == customer_id,
        )
        .order_by(Ticket.id.asc())
        .all()
    ]
    control_conversation_ids = [
        int(conversation_id)
        for (conversation_id,) in db.query(ConversationControl.conversation_id)
        .filter(ConversationControl.customer_id == customer_id)
        .order_by(ConversationControl.conversation_id.asc())
        .all()
    ]
    identities = []
    if ticket_ids:
        identities.append(WebchatConversation.ticket_id.in_(ticket_ids))
    if control_conversation_ids:
        identities.append(WebchatConversation.id.in_(control_conversation_ids))
    if not identities:
        return []
    return (
        db.query(WebchatConversation)
        .filter(
            WebchatConversation.status == "open",
            or_(*identities),
        )
        .order_by(WebchatConversation.id.asc())
        .all()
    )


def _enforce_human_only_processing(
    db: Session,
    *,
    actor,
    tenant_id: int,
    customer_id: int,
    restriction_id: int,
) -> int:
    """Route every open subject conversation through the canonical Handoff writer."""

    from .webchat_handoff_service import request_webchat_handoff

    count = 0
    for conversation in _subject_open_conversations(
        db,
        tenant_id=tenant_id,
        customer_id=customer_id,
    ):
        ticket = db.get(Ticket, conversation.ticket_id) if conversation.ticket_id else None
        if ticket is not None and (
            ticket.tenant_id != tenant_id or ticket.customer_id != customer_id
        ):
            raise DataLifecycleError("processing_restriction_conversation_scope_conflict")
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="privacy",
            trigger_type="processing_restricted",
            reason_code="data_processing_restricted",
            reason_text="Automated processing is restricted for this customer.",
            recommended_agent_action=(
                "Continue with human support only. Do not resume AI while the "
                "processing restriction remains active."
            ),
            requested_by_actor_type="system",
            requested_by_user_id=getattr(actor, "id", None),
            note=f"Processing restriction {restriction_id} activated.",
        )
        count += 1
    return count


def activate_data_processing_restriction(
    db: Session,
    *,
    actor,
    request_id: int,
    reason_code: str = "data_subject_requested_restriction",
) -> DataProcessingRestriction:
    tenant_id, request, customer = _request(
        db,
        actor=actor,
        request_id=request_id,
        request_type="restrict",
    )
    existing = (
        db.query(DataProcessingRestriction)
        .filter(DataProcessingRestriction.request_id == request.id)
        .first()
    )
    if existing is not None:
        if existing.status != "active":
            raise DataLifecycleError("processing_restriction_request_already_released")
        _enforce_human_only_processing(
            db,
            actor=actor,
            tenant_id=tenant_id,
            customer_id=customer.id,
            restriction_id=existing.id,
        )
        return existing
    reason = " ".join(str(reason_code or "").strip().split())[:120]
    if not reason:
        raise DataLifecycleError(
            "processing_restriction_reason_required",
            status_code=400,
        )
    row = DataProcessingRestriction(
        tenant_id=tenant_id,
        customer_id=customer.id,
        request_id=request.id,
        status="active",
        blocked_purposes_json=sorted(DEFAULT_BLOCKED_PURPOSES),
        allowed_purposes_json=sorted(ALLOWED_PURPOSES_WHEN_RESTRICTED),
        reason_code=reason,
        placed_by=getattr(actor, "id", None),
        released_by=None,
        placed_at=utc_now(),
        released_at=None,
    )
    db.add(row)
    db.flush()
    human_only_conversation_count = _enforce_human_only_processing(
        db,
        actor=actor,
        tenant_id=tenant_id,
        customer_id=customer.id,
        restriction_id=row.id,
    )
    manifest = {
        "schema": "nexus.processing-restriction-manifest.v1",
        "restriction_id": row.id,
        "blocked_purposes": row.blocked_purposes_json,
        "allowed_purposes": row.allowed_purposes_json,
        "human_only_conversation_count": human_only_conversation_count,
        "raw_values_persisted": False,
    }
    request.status = "completed"
    request.blocked_reason = None
    request.result_manifest_json = manifest
    request.result_sha256 = _digest(manifest)
    request.completed_at = utc_now()
    request.updated_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.dsar.processing_restricted",
        target_type="data_processing_restriction",
        target_id=row.id,
        new_value={
            "customer_id": customer.id,
            "request_id": request.id,
            "reason_code": reason,
            "blocked_purposes": row.blocked_purposes_json,
            "human_only_conversation_count": human_only_conversation_count,
        },
    )
    db.flush()
    return row


def release_data_processing_restriction(
    db: Session,
    *,
    actor,
    restriction_id: int,
) -> DataProcessingRestriction:
    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        raise DataLifecycleError("privacy_requires_tenant_authority", status_code=403)
    row = db.get(DataProcessingRestriction, restriction_id)
    if row is None or row.tenant_id != tenant_id:
        raise DataLifecycleError("processing_restriction_not_found", status_code=404)
    if row.status == "released":
        return row
    row.status = "released"
    row.released_by = getattr(actor, "id", None)
    row.released_at = utc_now()
    log_admin_audit(
        db,
        actor_id=getattr(actor, "id", None),
        action="privacy.processing_restriction.released",
        target_type="data_processing_restriction",
        target_id=row.id,
        new_value={
            "status": "released",
            "ai_resume_requires_explicit_handoff_command": True,
        },
    )
    db.flush()
    return row


def active_processing_restriction(
    db: Session,
    *,
    customer_id: int | None,
) -> DataProcessingRestriction | None:
    if customer_id is None:
        return None
    return (
        db.query(DataProcessingRestriction)
        .filter(
            DataProcessingRestriction.customer_id == customer_id,
            DataProcessingRestriction.status == "active",
        )
        .order_by(DataProcessingRestriction.id.asc())
        .first()
    )


def ensure_data_processing_allowed(
    db: Session,
    *,
    customer_id: int | None,
    purpose: str,
) -> None:
    normalized = " ".join(str(purpose or "").strip().lower().split())
    if not normalized:
        raise ValueError("processing_purpose_required")
    if normalized in ALLOWED_PURPOSES_WHEN_RESTRICTED:
        return
    restriction = active_processing_restriction(db, customer_id=customer_id)
    if restriction is None:
        return
    blocked = set(restriction.blocked_purposes_json or [])
    if normalized in blocked or normalized not in set(
        restriction.allowed_purposes_json or []
    ):
        raise DataProcessingRestricted(
            customer_id=int(customer_id or 0),
            purpose=normalized,
            restriction_id=restriction.id,
        )
