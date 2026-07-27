from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..enums import (
    ConversationState,
    EventType,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from ..models import BackgroundJob, Customer, Tenant, Ticket, TicketEvent
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .background_jobs import enqueue_background_job
from .customer_identity_service import (
    bind_customer_identity,
    resolve_or_create_customer,
)
from .tenant_authority import stamp_runtime_tenant
from .ticket_service import generate_ticket_no

WEBCHAT_HANDOFF_SNAPSHOT_JOB = "webchat.handoff_snapshot"
HANDOFF_SNAPSHOT_SCHEMA = "nexus.webchat-handoff-snapshot.v2"
ACTIVE_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
}


class WebchatHandoffSnapshotError(RuntimeError):
    pass


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        return None
    return cleaned[:limit]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime_public_id_for_session(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
) -> str:
    return f"wcf_{_hash(f'runtime:{tenant_key}:{channel_key}:{session_id}')[:24]}"


def _runtime_visitor_token_hash(source_key: str) -> str:
    return _hash(f"runtime-handoff:{source_key}")


def _metadata_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _required_snapshot_identity(snapshot: dict[str, Any]) -> tuple[str, str, str, str]:
    tenant_key = (_clip(snapshot.get("tenant_key"), 120) or "").lower()
    channel_key = (_clip(snapshot.get("channel_key"), 120) or "").lower()
    session_id = _clip(snapshot.get("session_id"), 120) or ""
    client_message_id = _clip(snapshot.get("client_message_id"), 120) or ""
    if not tenant_key or tenant_key == "default":
        raise WebchatHandoffSnapshotError("handoff_snapshot_tenant_required")
    if not channel_key or not session_id or not client_message_id:
        raise WebchatHandoffSnapshotError("handoff_snapshot_source_identity_required")
    return tenant_key, channel_key, session_id, client_message_id


def _source_event_id(snapshot: dict[str, Any]) -> str:
    tenant_key, channel_key, session_id, client_message_id = (
        _required_snapshot_identity(snapshot)
    )
    return "whs_" + _hash(
        f"{tenant_key}:{channel_key}:{session_id}:{client_message_id}:handoff"
    )[:40]


def _snapshot_issue_key(snapshot: dict[str, Any]) -> str:
    tracking = _clip(snapshot.get("tracking_number"), 120)
    intent = (
        _clip(snapshot.get("intent"), 80)
        or _clip(snapshot.get("handoff_reason"), 80)
        or "handoff"
    )
    session = _clip(snapshot.get("session_id"), 120) or "unknown"
    if tracking:
        return f"tracking:{tracking}:intent:{intent}"[:240]
    return f"session:{session}:intent:{intent}"[:240]


def webchat_handoff_source_dedupe_key(snapshot: dict[str, Any]) -> str:
    tenant_key, channel_key, _session_id, _client_message_id = (
        _required_snapshot_identity(snapshot)
    )
    return (
        f"webchat-runtime-handoff:{tenant_key}:{channel_key}:"
        f"{_source_event_id(snapshot)}"
    )[:300]


def build_handoff_snapshot_payload(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    customer_last_message: str,
    ai_reply: str,
    intent: str | None,
    tracking_number: str | None,
    handoff_reason: str | None,
    recommended_agent_action: str | None,
    recent_context: list[dict[str, Any]] | None = None,
    visitor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compact_context: list[dict[str, str]] = []
    for item in (recent_context or [])[-10:]:
        if not isinstance(item, dict):
            continue
        role = _clip(item.get("role"), 20)
        text = _clip(item.get("text") or item.get("body"), 240)
        if role and text:
            compact_context.append({"role": role, "text": text})
    snapshot = {
        "schema": HANDOFF_SNAPSHOT_SCHEMA,
        "snapshot_type": "webchat_ai_handoff_snapshot",
        "source": "webchat_runtime_provider",
        "tenant_key": (_clip(tenant_key, 120) or "").lower(),
        "channel_key": (_clip(channel_key, 120) or "").lower(),
        "session_id": _clip(session_id, 120),
        "client_message_id": _clip(client_message_id, 120),
        "customer_last_message": _clip(customer_last_message, 2000),
        "ai_reply": _clip(ai_reply, 1200),
        "intent": _clip(intent, 80) or "handoff",
        "tracking_number": _clip(tracking_number, 120),
        "handoff_required": True,
        "handoff_reason": (
            _clip(handoff_reason, 240) or "ai_requested_handoff"
        ),
        "recent_context_summary": compact_context,
        "recommended_agent_action": _clip(recommended_agent_action, 500),
        "visitor": visitor or {},
        "created_at": utc_now().isoformat(),
    }
    tenant_value, channel_value, session_value, _client_value = (
        _required_snapshot_identity(snapshot)
    )
    snapshot["source_event_id"] = _source_event_id(snapshot)
    snapshot["runtime_issue_key"] = _snapshot_issue_key(snapshot)
    snapshot["source_dedupe_key"] = webchat_handoff_source_dedupe_key(snapshot)
    snapshot["public_conversation_id"] = _runtime_public_id_for_session(
        tenant_key=tenant_value,
        channel_key=channel_value,
        session_id=session_value,
    )
    return snapshot


def enqueue_webchat_handoff_snapshot_job(
    db: Session,
    *,
    snapshot: dict[str, Any],
) -> BackgroundJob:
    tenant_key, _channel_key, _session_id, _client_id = (
        _required_snapshot_identity(snapshot)
    )
    dedupe_key = (
        snapshot.get("source_dedupe_key")
        or webchat_handoff_source_dedupe_key(snapshot)
    )
    return enqueue_background_job(
        db,
        queue_name="webchat_handoff_snapshot",
        job_type=WEBCHAT_HANDOFF_SNAPSHOT_JOB,
        payload={"tenant_key": tenant_key, "snapshot": snapshot},
        dedupe_key=str(dedupe_key),
    )


def _tenant(db: Session, snapshot: dict[str, Any]) -> Tenant:
    tenant_key, _channel_key, _session_id, _client_id = (
        _required_snapshot_identity(snapshot)
    )
    row = (
        db.query(Tenant)
        .filter(Tenant.tenant_key == tenant_key, Tenant.is_active.is_(True))
        .first()
    )
    if row is None:
        raise WebchatHandoffSnapshotError("handoff_snapshot_tenant_not_found")
    return row


def _existing_ticket(
    db: Session,
    snapshot: dict[str, Any],
    source_dedupe_key: str,
) -> Ticket | None:
    tenant = _tenant(db, snapshot)
    ticket = db.execute(
        select(Ticket)
        .where(
            Ticket.tenant_id == tenant.id,
            Ticket.source_dedupe_key == source_dedupe_key,
            Ticket.status.in_(ACTIVE_TICKET_STATUSES),
        )
        .limit(1)
    ).scalar_one_or_none()
    if ticket is not None:
        return ticket
    tracking = _clip(snapshot.get("tracking_number"), 120)
    if tracking:
        return db.execute(
            select(Ticket)
            .where(
                Ticket.tenant_id == tenant.id,
                Ticket.tracking_number == tracking,
                Ticket.source_channel == SourceChannel.web_chat,
                Ticket.status.in_(ACTIVE_TICKET_STATUSES),
            )
            .limit(1)
        ).scalar_one_or_none()
    return None


def _customer(
    db: Session,
    *,
    tenant: Tenant,
    snapshot: dict[str, Any],
    public_id: str,
) -> Customer:
    visitor = snapshot.get("visitor") or {}
    email = _clip(visitor.get("email"), 200)
    phone = _clip(visitor.get("phone"), 60)
    session_id = _clip(snapshot.get("session_id"), 120) or public_id
    external_ref = f"webchat-runtime:{tenant.tenant_key}:{session_id}"[:160]
    if email:
        identity_type, identity_value = "email", email
    elif phone:
        identity_type, identity_value = "phone", phone
    else:
        identity_type, identity_value = "external_ref", external_ref
    customer = resolve_or_create_customer(
        db,
        tenant_id=tenant.id,
        identity_type=identity_type,
        identity_value=identity_value,
        display_name=_clip(visitor.get("name"), 160),
        source="webchat_runtime_handoff",
    )
    bind_customer_identity(
        db,
        customer=customer,
        identity_type="external_ref",
        identity_value=external_ref,
        source="webchat_runtime_handoff",
    )
    if email and identity_type != "email":
        bind_customer_identity(
            db,
            customer=customer,
            identity_type="email",
            identity_value=email,
            source="webchat_runtime_handoff",
        )
    if phone and identity_type != "phone":
        bind_customer_identity(
            db,
            customer=customer,
            identity_type="phone",
            identity_value=phone,
            source="webchat_runtime_handoff",
        )
    return customer


def _find_message(
    db: Session,
    *,
    conversation_id: int,
    client_message_id: str,
) -> WebchatMessage | None:
    return db.execute(
        select(WebchatMessage)
        .where(
            WebchatMessage.conversation_id == conversation_id,
            WebchatMessage.client_message_id == client_message_id,
        )
        .limit(1)
    ).scalar_one_or_none()


def _add_message_once(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    direction: str,
    body: str,
    client_message_id: str,
    author_label: str,
    metadata: dict[str, Any],
) -> WebchatMessage:
    clipped_client_id = (
        _clip(client_message_id, 120) or f"runtime-handoff-{direction}"
    )
    existing = _find_message(
        db,
        conversation_id=conversation.id,
        client_message_id=clipped_client_id,
    )
    if existing is not None:
        if existing.ticket_id is None:
            existing.ticket_id = ticket.id
            db.flush()
        return existing
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction=direction,
        body=body,
        body_text=body,
        message_type="text",
        metadata_json=_metadata_json(metadata),
        client_message_id=clipped_client_id,
        delivery_status="sent",
        author_label=author_label,
    )
    try:
        with db.begin_nested():
            db.add(message)
            db.flush()
    except IntegrityError:
        existing = _find_message(
            db,
            conversation_id=conversation.id,
            client_message_id=clipped_client_id,
        )
        if existing is None:
            raise
        if existing.ticket_id is None:
            existing.ticket_id = ticket.id
            db.flush()
        return existing
    return message


def _conversation(
    db: Session,
    *,
    tenant: Tenant,
    customer: Customer,
    ticket: Ticket,
    snapshot: dict[str, Any],
    source_dedupe_key: str,
) -> WebchatConversation:
    tenant_key, channel_key, session_id, client_message_id = (
        _required_snapshot_identity(snapshot)
    )
    public_id = (
        _clip(snapshot.get("public_conversation_id"), 64)
        or _runtime_public_id_for_session(
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
        )
    )
    conversation = db.execute(
        select(WebchatConversation)
        .where(
            WebchatConversation.tenant_key == tenant_key,
            WebchatConversation.channel_key == channel_key,
            WebchatConversation.public_id == public_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    now = utc_now()
    visitor = snapshot.get("visitor") or {}
    if conversation is None:
        conversation = WebchatConversation(
            public_id=public_id,
            visitor_token_hash=_runtime_visitor_token_hash(source_dedupe_key),
            visitor_token_expires_at=None,
            tenant_key=tenant_key,
            channel_key=channel_key,
            ticket_id=ticket.id,
            visitor_name=_clip(visitor.get("name"), 160),
            visitor_email=_clip(visitor.get("email"), 200),
            visitor_phone=_clip(visitor.get("phone"), 80),
            visitor_ref=session_id,
            origin="webchat-runtime",
            status="open",
            runtime_session_id=session_id,
            runtime_issue_key=_clip(snapshot.get("runtime_issue_key"), 240),
            last_intent=_clip(snapshot.get("intent"), 120),
            last_tracking_number=_clip(snapshot.get("tracking_number"), 120),
            last_seen_at=now,
            created_at=now,
            updated_at=now,
            runtime_context_updated_at=now,
        )
        db.add(conversation)
        db.flush()
    elif conversation.ticket_id not in {None, ticket.id}:
        raise WebchatHandoffSnapshotError("handoff_conversation_ticket_conflict")
    conversation.ticket_id = ticket.id
    conversation.status = "open"
    conversation.last_seen_at = now
    conversation.updated_at = now
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    if control is None:
        control = ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer.id,
            tenant_key=tenant.tenant_key,
            country_code=None,
            channel_key=channel_key,
            created_at=now,
            updated_at=now,
        )
        db.add(control)
    elif (
        control.tenant_key != tenant.tenant_key
        or control.channel_key != channel_key
        or control.customer_id not in {None, customer.id}
    ):
        raise WebchatHandoffSnapshotError("handoff_conversation_scope_conflict")
    else:
        control.customer_id = customer.id
        control.updated_at = now

    customer_message = _clip(snapshot.get("customer_last_message"), 2000)
    ai_reply = _clip(snapshot.get("ai_reply"), 1200)
    if customer_message:
        _add_message_once(
            db,
            conversation=conversation,
            ticket=ticket,
            direction="visitor",
            body=customer_message,
            client_message_id=client_message_id,
            author_label="Customer",
            metadata={
                "source": "webchat_runtime_handoff",
                "source_event_id": snapshot.get("source_event_id"),
            },
        )
    if ai_reply:
        _add_message_once(
            db,
            conversation=conversation,
            ticket=ticket,
            direction="ai",
            body=ai_reply,
            client_message_id=f"{client_message_id}:ai"[:120],
            author_label="AI Assistant",
            metadata={
                "source": "webchat_runtime_handoff",
                "handoff_required": True,
            },
        )
    _add_message_once(
        db,
        conversation=conversation,
        ticket=ticket,
        direction="system",
        body="WebChat Runtime requested human review.",
        client_message_id=f"{client_message_id}:handoff"[:120],
        author_label="System",
        metadata={
            "source": "webchat_runtime_handoff",
            "handoff_reason": snapshot.get("handoff_reason"),
            "recommended_agent_action": snapshot.get("recommended_agent_action"),
            "source_event_id": snapshot.get("source_event_id"),
        },
    )
    db.flush()
    return conversation


def create_ticket_from_webchat_snapshot(
    db: Session,
    *,
    snapshot: dict[str, Any],
) -> Ticket:
    tenant = _tenant(db, snapshot)
    source_dedupe_key = (
        snapshot.get("source_dedupe_key")
        or webchat_handoff_source_dedupe_key(snapshot)
    )
    existing = _existing_ticket(db, snapshot, str(source_dedupe_key))
    public_id = (
        _clip(snapshot.get("public_conversation_id"), 64)
        or _runtime_public_id_for_session(
            tenant_key=tenant.tenant_key,
            channel_key=_required_snapshot_identity(snapshot)[1],
            session_id=_required_snapshot_identity(snapshot)[2],
        )
    )
    customer = _customer(
        db,
        tenant=tenant,
        snapshot=snapshot,
        public_id=public_id,
    )
    if existing is not None:
        _conversation(
            db,
            tenant=tenant,
            customer=customer,
            ticket=existing,
            snapshot=snapshot,
            source_dedupe_key=str(source_dedupe_key),
        )
        return existing

    title_part = snapshot.get("tracking_number") or snapshot.get("intent") or "handoff"
    ticket = Ticket(
        ticket_no=generate_ticket_no(),
        title=f"WebChat handoff · {title_part}"[:255],
        description=(
            "AI handoff snapshot\n\n"
            f"Customer message: {snapshot.get('customer_last_message') or ''}\n\n"
            f"AI reply: {snapshot.get('ai_reply') or ''}\n\n"
            f"Reason: {snapshot.get('handoff_reason') or ''}"
        )[:4000],
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number=_clip(snapshot.get("tracking_number"), 120),
        source_chat_id=f"webchat-runtime:{public_id}"[:120],
        source_dedupe_key=str(source_dedupe_key),
        case_type=_clip(snapshot.get("intent"), 120),
        customer_request=_clip(snapshot.get("customer_last_message"), 4000),
        last_customer_message=_clip(snapshot.get("customer_last_message"), 4000),
        required_action=_clip(snapshot.get("recommended_agent_action"), 500),
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=public_id,
    )
    stamp_runtime_tenant(ticket, tenant.id)
    try:
        with db.begin_nested():
            db.add(ticket)
            db.flush()
            conversation = _conversation(
                db,
                tenant=tenant,
                customer=customer,
                ticket=ticket,
                snapshot=snapshot,
                source_dedupe_key=str(source_dedupe_key),
            )
            db.add(
                TicketEvent(
                    ticket_id=ticket.id,
                    actor_id=None,
                    event_type=EventType.ticket_created,
                    note="WebChat AI handoff snapshot created",
                    payload_json=_metadata_json(
                        {
                            "schema": HANDOFF_SNAPSHOT_SCHEMA,
                            "public_conversation_id": conversation.public_id,
                            "source_event_id": snapshot.get("source_event_id"),
                            "source_dedupe_key": source_dedupe_key,
                            "tenant_key": tenant.tenant_key,
                            "contains_payloads": False,
                        }
                    ),
                )
            )
            db.flush()
    except IntegrityError:
        existing = _existing_ticket(db, snapshot, str(source_dedupe_key))
        if existing is None:
            raise
        _conversation(
            db,
            tenant=tenant,
            customer=customer,
            ticket=existing,
            snapshot=snapshot,
            source_dedupe_key=str(source_dedupe_key),
        )
        return existing
    return ticket


def process_webchat_handoff_snapshot_job(
    db: Session,
    *,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    ticket = create_ticket_from_webchat_snapshot(db, snapshot=snapshot)
    conversation = db.execute(
        select(WebchatConversation)
        .where(WebchatConversation.ticket_id == ticket.id)
        .order_by(WebchatConversation.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "status": "done",
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "public_conversation_id": (
            conversation.public_id if conversation is not None else None
        ),
    }
