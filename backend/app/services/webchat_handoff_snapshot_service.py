from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import BackgroundJob, Customer, Ticket, TicketEvent
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .background_jobs import enqueue_background_job
from .ticket_service import generate_ticket_no

WEBCHAT_HANDOFF_SNAPSHOT_JOB = "webchat.handoff_snapshot"


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        return None
    return cleaned[:limit]


def _normalized(value: Any, limit: int) -> str | None:
    clipped = _clip(value, limit)
    return clipped.lower() if clipped else None


def _fast_public_id(source_dedupe_key: str) -> str:
    return f"wcf_{hashlib.sha256(source_dedupe_key.encode('utf-8')).hexdigest()[:24]}"


def _fast_visitor_token_hash(source_dedupe_key: str) -> str:
    return hashlib.sha256(f"fast-handoff:{source_dedupe_key}".encode("utf-8")).hexdigest()


def _metadata_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def webchat_handoff_source_dedupe_key(snapshot: dict[str, Any]) -> str:
    return "webchat-fast-handoff:{tenant}:{session}:{client}".format(
        tenant=_clip(snapshot.get("tenant_key"), 120) or "default",
        session=_clip(snapshot.get("session_id"), 120) or "unknown",
        client=_clip(snapshot.get("client_message_id"), 120) or "unknown",
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
    compact_context = []
    for item in (recent_context or [])[-10:]:
        if not isinstance(item, dict):
            continue
        role = _clip(item.get("role"), 20)
        text = _clip(item.get("text") or item.get("body"), 240)
        if role and text:
            compact_context.append({"role": role, "text": text})
    snapshot = {
        "snapshot_type": "webchat_ai_handoff_snapshot",
        "source": "webchat_fast_openclaw_responses",
        "tenant_key": _clip(tenant_key, 120) or "default",
        "channel_key": _clip(channel_key, 120) or "website",
        "session_id": _clip(session_id, 120),
        "client_message_id": _clip(client_message_id, 120),
        "customer_last_message": _clip(customer_last_message, 2000),
        "ai_reply": _clip(ai_reply, 1200),
        "intent": _clip(intent, 80) or "handoff",
        "tracking_number": _clip(tracking_number, 120),
        "handoff_required": True,
        "handoff_reason": _clip(handoff_reason, 240) or "ai_requested_handoff",
        "recent_context_summary": compact_context,
        "recommended_agent_action": _clip(recommended_agent_action, 500) or "Review the WebChat AI handoff snapshot and reply to the customer.",
        "visitor": visitor or {},
        "created_at": utc_now().isoformat(),
    }
    snapshot["source_dedupe_key"] = webchat_handoff_source_dedupe_key(snapshot)
    snapshot["public_conversation_id"] = _fast_public_id(snapshot["source_dedupe_key"])
    return snapshot


def enqueue_webchat_handoff_snapshot_job(db: Session, *, snapshot: dict[str, Any]) -> BackgroundJob:
    dedupe_key = snapshot.get("source_dedupe_key") or webchat_handoff_source_dedupe_key(snapshot)
    return enqueue_background_job(
        db,
        queue_name="webchat_handoff_snapshot",
        job_type=WEBCHAT_HANDOFF_SNAPSHOT_JOB,
        payload={"snapshot": snapshot},
        dedupe_key=dedupe_key,
    )


def _existing_ticket_by_source_dedupe_key(db: Session, source_dedupe_key: str) -> Ticket | None:
    return db.execute(select(Ticket).where(Ticket.source_dedupe_key == source_dedupe_key).limit(1)).scalar_one_or_none()


def _find_or_create_customer(db: Session, *, snapshot: dict[str, Any], public_id: str) -> Customer:
    visitor = snapshot.get("visitor") or {}
    email = _clip(visitor.get("email"), 200)
    phone = _clip(visitor.get("phone"), 60)
    email_normalized = _normalized(email, 200)
    phone_normalized = _normalized(phone, 60)
    external_ref = _clip(visitor.get("external_ref") or visitor.get("visitor_ref") or public_id, 120)

    existing: Customer | None = None
    if email_normalized:
        existing = db.execute(select(Customer).where(Customer.email_normalized == email_normalized).limit(1)).scalar_one_or_none()
    if existing is None and phone_normalized:
        existing = db.execute(select(Customer).where(Customer.phone_normalized == phone_normalized).limit(1)).scalar_one_or_none()
    if existing is None and external_ref:
        existing = db.execute(select(Customer).where(Customer.external_ref == external_ref).limit(1)).scalar_one_or_none()
    if existing is not None:
        return existing

    name = _clip(visitor.get("name"), 160) or email or phone or f"Webchat Fast Visitor {public_id[-6:]}"
    customer = Customer(
        name=name,
        email=email,
        email_normalized=email_normalized,
        phone=phone,
        phone_normalized=phone_normalized,
        external_ref=external_ref,
    )
    db.add(customer)
    db.flush()
    return customer


def _message_exists(db: Session, *, conversation_id: int, client_message_id: str) -> bool:
    return db.execute(
        select(WebchatMessage.id)
        .where(WebchatMessage.conversation_id == conversation_id, WebchatMessage.client_message_id == client_message_id)
        .limit(1)
    ).scalar_one_or_none() is not None


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
) -> None:
    clipped_client_id = _clip(client_message_id, 120) or f"fast-handoff-{direction}"
    if _message_exists(db, conversation_id=conversation.id, client_message_id=clipped_client_id):
        return
    db.add(
        WebchatMessage(
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
    )


def _ensure_fast_handoff_conversation_linkage(db: Session, *, ticket: Ticket, snapshot: dict[str, Any], source_dedupe_key: str) -> WebchatConversation:
    public_id = _clip(snapshot.get("public_conversation_id"), 64) or _fast_public_id(source_dedupe_key)
    customer = _find_or_create_customer(db, snapshot=snapshot, public_id=public_id)
    if ticket.customer_id is None:
        ticket.customer_id = customer.id

    conversation = db.execute(select(WebchatConversation).where(WebchatConversation.public_id == public_id).limit(1)).scalar_one_or_none()
    if conversation is None:
        conversation = WebchatConversation(
            public_id=public_id,
            visitor_token_hash=_fast_visitor_token_hash(source_dedupe_key),
            visitor_token_expires_at=None,
            tenant_key=_clip(snapshot.get("tenant_key"), 120) or "default",
            channel_key=_clip(snapshot.get("channel_key"), 120) or "website",
            ticket_id=ticket.id,
            visitor_name=_clip((snapshot.get("visitor") or {}).get("name"), 160),
            visitor_email=_clip((snapshot.get("visitor") or {}).get("email"), 200),
            visitor_phone=_clip((snapshot.get("visitor") or {}).get("phone"), 80),
            visitor_ref=public_id,
            origin="webchat-fast",
            page_url=None,
            user_agent=None,
            status="open",
            last_seen_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(conversation)
        db.flush()
    else:
        conversation.ticket_id = ticket.id
        conversation.status = conversation.status or "open"
        conversation.updated_at = utc_now()
        conversation.last_seen_at = utc_now()

    source_chat_id = f"webchat-fast:{public_id}"[:120]
    ticket.source_chat_id = source_chat_id
    ticket.preferred_reply_channel = SourceChannel.web_chat.value
    ticket.preferred_reply_contact = public_id

    client_message_id = _clip(snapshot.get("client_message_id"), 100) or "unknown"
    customer_message = _clip(snapshot.get("customer_last_message"), 2000) or "WebChat handoff requested."
    ai_reply = _clip(snapshot.get("ai_reply"), 1200) or "A human teammate will review this request."
    _add_message_once(
        db,
        conversation=conversation,
        ticket=ticket,
        direction="visitor",
        body=customer_message,
        client_message_id=client_message_id,
        author_label="Customer",
        metadata={"source": "webchat_fast_handoff", "source_dedupe_key": source_dedupe_key},
    )
    _add_message_once(
        db,
        conversation=conversation,
        ticket=ticket,
        direction="ai",
        body=ai_reply,
        client_message_id=f"{client_message_id}:ai"[:120],
        author_label="Speedy",
        metadata={"source": "webchat_fast_handoff", "handoff_required": True},
    )
    _add_message_once(
        db,
        conversation=conversation,
        ticket=ticket,
        direction="system",
        body="WebChat Fast Lane created a human-review handoff ticket.",
        client_message_id=f"{client_message_id}:handoff"[:120],
        author_label="System",
        metadata={
            "source": "webchat_fast_handoff",
            "handoff_reason": snapshot.get("handoff_reason"),
            "recommended_agent_action": snapshot.get("recommended_agent_action"),
            "source_dedupe_key": source_dedupe_key,
        },
    )
    db.flush()
    return conversation


def create_ticket_from_webchat_snapshot(db: Session, *, snapshot: dict[str, Any]) -> Ticket:
    source_dedupe_key = snapshot.get("source_dedupe_key") or webchat_handoff_source_dedupe_key(snapshot)
    existing = _existing_ticket_by_source_dedupe_key(db, source_dedupe_key)
    if existing is not None:
        _ensure_fast_handoff_conversation_linkage(db, ticket=existing, snapshot=snapshot, source_dedupe_key=source_dedupe_key)
        return existing

    public_id = _clip(snapshot.get("public_conversation_id"), 64) or _fast_public_id(source_dedupe_key)
    customer = _find_or_create_customer(db, snapshot=snapshot, public_id=public_id)
    title_part = snapshot.get("tracking_number") or snapshot.get("intent") or "handoff"
    title = f"WebChat handoff · {title_part}"[:255]
    description = (
        "AI handoff snapshot\n\n"
        f"Customer message: {snapshot.get('customer_last_message') or ''}\n\n"
        f"AI reply: {snapshot.get('ai_reply') or ''}\n\n"
        f"Reason: {snapshot.get('handoff_reason') or ''}"
    )[:4000]
    ticket = Ticket(
        ticket_no=generate_ticket_no(),
        title=title,
        description=description,
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number=snapshot.get("tracking_number"),
        source_chat_id=f"webchat-fast:{public_id}"[:120],
        source_dedupe_key=source_dedupe_key,
        customer_request=snapshot.get("customer_last_message"),
        last_customer_message=snapshot.get("customer_last_message"),
        required_action=snapshot.get("recommended_agent_action"),
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=public_id,
    )
    try:
        with db.begin_nested():
            db.add(ticket)
            db.flush()
            conversation = _ensure_fast_handoff_conversation_linkage(db, ticket=ticket, snapshot=snapshot, source_dedupe_key=source_dedupe_key)
            event_payload = dict(snapshot)
            event_payload["public_conversation_id"] = conversation.public_id
            event_payload["customer_id"] = ticket.customer_id
            db.add(TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.ticket_created,
                note="WebChat AI handoff snapshot created",
                payload_json=json.dumps(event_payload, ensure_ascii=False),
            ))
            db.flush()
    except IntegrityError:
        existing = _existing_ticket_by_source_dedupe_key(db, source_dedupe_key)
        if existing is None:
            raise
        _ensure_fast_handoff_conversation_linkage(db, ticket=existing, snapshot=snapshot, source_dedupe_key=source_dedupe_key)
        return existing
    return ticket


def process_webchat_handoff_snapshot_job(db: Session, *, snapshot: dict[str, Any]) -> dict[str, Any]:
    ticket = create_ticket_from_webchat_snapshot(db, snapshot=snapshot)
    conversation = db.execute(select(WebchatConversation).where(WebchatConversation.ticket_id == ticket.id).order_by(WebchatConversation.id.desc()).limit(1)).scalar_one_or_none()
    return {
        "status": "done",
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "public_conversation_id": conversation.public_id if conversation is not None else None,
    }
