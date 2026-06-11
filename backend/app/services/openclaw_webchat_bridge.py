from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketStatus
from ..models import OpenClawConversationLink, OpenClawTranscriptMessage, Ticket, TicketComment, TicketEvent
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .background_jobs import WEBCHAT_AI_REPLY_JOB, enqueue_background_job
from .webchat_ai_turn_service import is_ai_suspended_for_handoff, safe_write_webchat_event, schedule_webchat_ai_turn
from .webchat_channel_delivery import create_customer_reply_outbound
from .webchat_handoff_service import request_webchat_handoff
from .webchat_intent_service import detect_webchat_intent

MAX_FIELD_CHARS = 300
MAX_BODY_CHARS = 2000
WHATSAPP_ORIGIN = "openclaw-whatsapp"


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> str | None:
    text = " ".join(str(value or "").strip().split())
    return text[:limit] if text else None


def _clip_body(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:MAX_BODY_CHARS] if text else None


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _is_whatsapp(link: OpenClawConversationLink, ticket: Ticket) -> bool:
    candidates = [
        getattr(link, "channel", None),
        getattr(ticket, "preferred_reply_channel", None),
        _value(getattr(ticket, "source_channel", None)),
    ]
    return any(str(item or "").strip().lower() == SourceChannel.whatsapp.value for item in candidates)


def _public_id_for_session(session_key: str) -> str:
    digest = hashlib.sha256(session_key.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"wa_{digest}"


def _visitor_token_hash(session_key: str) -> str:
    return hashlib.sha256(f"openclaw-whatsapp:{session_key}".encode("utf-8", errors="ignore")).hexdigest()


def _client_message_id(row: OpenClawTranscriptMessage) -> str:
    raw = f"{row.session_key}:{row.message_id}:{row.id}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"openclaw:{digest}"


def _conversation_for_whatsapp_link(db: Session, *, link: OpenClawConversationLink, ticket: Ticket) -> WebchatConversation:
    existing = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.ticket_id == ticket.id, WebchatConversation.channel_key == SourceChannel.whatsapp.value)
        .order_by(WebchatConversation.id.desc())
        .first()
    )
    recipient = _clip(link.recipient or ticket.preferred_reply_contact or ticket.source_chat_id, 160)
    visitor_name = _clip(getattr(getattr(ticket, "customer", None), "name", None), 160) or recipient or "WhatsApp Customer"
    if existing is not None:
        existing.visitor_name = existing.visitor_name or visitor_name
        existing.visitor_phone = existing.visitor_phone or recipient
        existing.visitor_ref = existing.visitor_ref or _clip(link.session_key, 160)
        existing.origin = existing.origin or WHATSAPP_ORIGIN
        existing.updated_at = utc_now()
        return existing

    public_id = _public_id_for_session(link.session_key)
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=_visitor_token_hash(link.session_key),
        visitor_token_expires_at=None,
        tenant_key="openclaw",
        channel_key=SourceChannel.whatsapp.value,
        ticket_id=ticket.id,
        visitor_name=visitor_name,
        visitor_phone=recipient,
        visitor_ref=_clip(link.session_key, 160),
        origin=WHATSAPP_ORIGIN,
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(conversation)
    db.flush()
    return conversation


def _create_ai_turn_job(db: Session):
    def create_job(payload: dict[str, Any], dedupe_key: str, scheduled_at):
        return enqueue_background_job(
            db,
            queue_name="webchat_ai_reply",
            job_type=WEBCHAT_AI_REPLY_JOB,
            payload=payload,
            dedupe_key=dedupe_key,
            next_run_at=scheduled_at,
        )

    return create_job


def _state_value(ticket: Ticket) -> str:
    return _value(getattr(ticket, "conversation_state", None))


def _status_value(ticket: Ticket) -> str:
    return _value(getattr(ticket, "status", None))


def _sync_ticket_for_inbound(ticket: Ticket, *, body: str) -> None:
    ticket.last_customer_message = body
    ticket.customer_request = ticket.customer_request or body
    if _status_value(ticket) in {TicketStatus.resolved.value, TicketStatus.closed.value}:
        ticket.status = TicketStatus.pending_assignment
        ticket.conversation_state = ConversationState.reopened_by_customer
    elif _state_value(ticket) not in {ConversationState.human_review_required.value, ConversationState.human_owned.value}:
        ticket.conversation_state = ConversationState.ai_active
    ticket.preferred_reply_channel = SourceChannel.whatsapp.value
    ticket.preferred_reply_contact = ticket.preferred_reply_contact or ticket.source_chat_id
    ticket.updated_at = utc_now()


def _write_projected_visitor_message(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    transcript: OpenClawTranscriptMessage,
) -> WebchatMessage | None:
    body = _clip_body(transcript.body_text)
    if not body:
        return None
    client_message_id = _client_message_id(transcript)
    existing = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.client_message_id == client_message_id)
        .first()
    )
    if existing is not None:
        return None
    metadata = {
        "generated_by": "openclaw_whatsapp_inbound",
        "external_send": False,
        "openclaw_transcript_message_id": transcript.id,
        "openclaw_message_id": transcript.message_id,
        "openclaw_session_key": transcript.session_key,
        "source_channel": SourceChannel.whatsapp.value,
    }
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body=body,
        body_text=body,
        message_type="text",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        client_message_id=client_message_id,
        delivery_status="sent",
        author_label=_clip(transcript.author_name, 120) or conversation.visitor_name or "WhatsApp Customer",
        created_at=transcript.received_at or utc_now(),
    )
    db.add(message)
    db.flush()
    db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=body, visibility=NoteVisibility.external))
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.comment_added,
        note="WhatsApp visitor message received through OpenClaw",
        payload_json=json.dumps({
            "public_conversation_id": conversation.public_id,
            "webchat_message_id": message.id,
            "openclaw_transcript_message_id": transcript.id,
            "openclaw_message_id": transcript.message_id,
            "source_channel": SourceChannel.whatsapp.value,
        }, ensure_ascii=False),
    ))
    _sync_ticket_for_inbound(ticket, body=body)
    conversation.last_seen_at = transcript.received_at or utc_now()
    conversation.updated_at = utc_now()
    return message


def _handoff_ack_text(customer_message: str) -> str:
    if any("\u4e00" <= ch <= "\u9fff" for ch in customer_message):
        return "您好，已收到您的消息。这个问题需要人工客服核实，我会把会话转给同事继续处理。"
    return "Thanks, we received your message. This needs a support teammate to review, so I am handing the conversation to our team here."


def _ensure_handoff_ack(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    trigger_message: WebchatMessage,
    handoff_request_id: int,
) -> None:
    client_message_id = f"whatsapp-handoff-ack:{handoff_request_id}"
    existing = (
        db.query(WebchatMessage.id)
        .filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.client_message_id == client_message_id)
        .first()
    )
    if existing is not None:
        return
    body = _handoff_ack_text(trigger_message.body)
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body=body,
        body_text=body,
        message_type="text",
        metadata_json=json.dumps({
            "generated_by": "whatsapp_handoff_ack",
            "external_send": True,
            "handoff_request_id": handoff_request_id,
            "source_channel": SourceChannel.whatsapp.value,
        }, ensure_ascii=False),
        client_message_id=client_message_id,
        delivery_status="queued",
        author_label="Support Assistant",
        created_at=utc_now(),
    )
    db.add(message)
    db.flush()
    outbound = create_customer_reply_outbound(
        db,
        ticket=ticket,
        conversation=conversation,
        body=body,
        created_by=None,
        local_provider_status="webchat_handoff_ack_delivered",
        external_provider_status="whatsapp_handoff_ack",
        metadata={"webchat_message_id": message.id, "handoff_request_id": handoff_request_id},
    )
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload={"message_id": message.id, "direction": "agent", "outbound_message_id": outbound.id, "source_channel": SourceChannel.whatsapp.value},
    )
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.outbound_queued if outbound.status == MessageStatus.pending else EventType.outbound_sent,
        note="WhatsApp handoff acknowledgement queued",
        payload_json=json.dumps({
            "public_conversation_id": conversation.public_id,
            "webchat_message_id": message.id,
            "outbound_message_id": outbound.id,
            "handoff_request_id": handoff_request_id,
            "external_send": True,
            "source_channel": SourceChannel.whatsapp.value,
        }, ensure_ascii=False),
    ))


def _maybe_request_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    message: WebchatMessage,
) -> bool:
    intent = detect_webchat_intent(message.body)
    conversation.last_intent = intent.intent
    if intent.risk_level != "high" and intent.intent not in {"handoff", "complaint", "address_change", "reschedule"}:
        return False
    ticket.required_action = "WhatsApp customer needs human review / handoff"
    ticket.conversation_state = ConversationState.human_review_required
    request_row = request_webchat_handoff(
        db,
        conversation=conversation,
        ticket=ticket,
        source="whatsapp_inbound_rule",
        trigger_type="intent_handoff",
        reason_code=f"intent:{intent.intent}",
        reason_text=f"WhatsApp intent requires human review: {intent.intent}",
        recommended_agent_action="Review the WhatsApp customer message and reply from the unified inbox.",
        trigger_message_id=message.id,
        requested_by_actor_type="system",
    )
    _ensure_handoff_ack(db, conversation=conversation, ticket=ticket, trigger_message=message, handoff_request_id=request_row.id)
    return True


def project_openclaw_whatsapp_to_webchat(
    db: Session,
    *,
    link: OpenClawConversationLink,
    ticket: Ticket,
    transcript_rows: Iterable[OpenClawTranscriptMessage],
) -> dict[str, int | bool | str | None]:
    if not _is_whatsapp(link, ticket):
        return {"ok": True, "skipped": True, "reason": "not_whatsapp", "messages_projected": 0, "ai_turns_scheduled": 0, "handoffs_requested": 0}

    conversation = _conversation_for_whatsapp_link(db, link=link, ticket=ticket)
    ticket.preferred_reply_channel = SourceChannel.whatsapp.value
    if link.recipient:
        ticket.preferred_reply_contact = link.recipient
        ticket.source_chat_id = ticket.source_chat_id or link.recipient
    if link.channel_account_id:
        ticket.channel_account_id = link.channel_account_id

    created_messages: list[WebchatMessage] = []
    for transcript in sorted(transcript_rows, key=lambda row: ((row.received_at or row.created_at or utc_now()), row.id)):
        if str(transcript.role or "").strip().lower() != "user":
            continue
        message = _write_projected_visitor_message(db, conversation=conversation, ticket=ticket, transcript=transcript)
        if message is not None:
            created_messages.append(message)

    if not created_messages:
        db.flush()
        return {"ok": True, "skipped": False, "conversation_id": conversation.public_id, "messages_projected": 0, "ai_turns_scheduled": 0, "handoffs_requested": 0}

    latest_message = created_messages[-1]
    handoff_requested = False
    for message in created_messages:
        if _maybe_request_handoff(db, conversation=conversation, ticket=ticket, message=message):
            handoff_requested = True
            break

    for message in created_messages[:-1]:
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="message.created",
            payload={"message_id": message.id, "direction": "visitor", "source_channel": SourceChannel.whatsapp.value},
        )

    snapshot = schedule_webchat_ai_turn(
        db,
        conversation=conversation,
        ticket_id=ticket.id,
        visitor_message=latest_message,
        create_job=_create_ai_turn_job(db),
    )
    db.flush()
    return {
        "ok": True,
        "skipped": False,
        "conversation_id": conversation.public_id,
        "messages_projected": len(created_messages),
        "ai_turns_scheduled": 0 if snapshot.get("ai_suppressed_by_handoff") else 1,
        "handoffs_requested": 1 if handoff_requested or is_ai_suspended_for_handoff(conversation) else 0,
    }
