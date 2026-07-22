from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, NoteVisibility, TicketStatus
from ..models import Ticket, TicketComment, TicketEvent
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatCardAction,
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)
from ..webchat_schemas import WebChatActionSubmitRequest, WebChatCardPayload
from .webchat_ai_turn_service import is_ai_suspended_for_handoff, safe_write_webchat_event
from .webchat_handoff_service import request_webchat_handoff
from .webchat_session_identity import (
    clip,
    clip_body,
    ensure_aware_utc,
    hash_optional,
    origin_from_request,
    validate_visitor_token,
)

LOGGER = logging.getLogger("nexusdesk")
RETIRED_WEBCHAT_CARD_TYPE = "quick" + "_replies"
RETIRED_WEBCHAT_ACTION_TYPE = "quick" + "_reply"
STALE_PUBLIC_HANDOFF_RESUME_AFTER = timedelta(minutes=30)


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _metadata(**items: Any) -> str:
    payload = {"external_send": False}
    payload.update({key: value for key, value in items.items() if value is not None})
    return json.dumps(payload, ensure_ascii=False, default=str)


def message_payload(row: WebchatMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "body_text": row.body_text or row.body,
        "message_type": row.message_type or "text",
        "payload_json": _loads_json(row.payload_json),
        "metadata_json": _loads_json(row.metadata_json),
        "client_message_id": row.client_message_id,
        "ai_turn_id": row.ai_turn_id,
        "author_user_id": row.author_user_id,
        "delivery_status": row.delivery_status or "sent",
        "action_status": row.action_status,
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get_authorized_webchat_conversation(
    db: Session,
    *,
    public_id: str,
    visitor_token: str | None,
) -> WebchatConversation:
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == public_id)
        .first()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    validate_visitor_token(conversation, visitor_token)
    return conversation


def _resume_stale_requested_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    visitor_message: WebchatMessage,
) -> bool:
    if ticket is None:
        return False
    if conversation.handoff_status != "requested" or conversation.active_agent_id:
        return False

    request_row = (
        db.get(WebchatHandoffRequest, conversation.current_handoff_request_id)
        if conversation.current_handoff_request_id
        else None
    )
    if request_row is not None and request_row.status != "requested":
        return False
    stale_anchor = ensure_aware_utc(
        request_row.requested_at
        if request_row is not None
        else conversation.ai_suspended_at
    )
    now = ensure_aware_utc(utc_now())
    if (
        stale_anchor is None
        or now is None
        or now - stale_anchor < STALE_PUBLIC_HANDOFF_RESUME_AFTER
    ):
        return False

    if request_row is not None:
        request_row.status = "resumed_ai"
        request_row.closed_at = now
        request_row.decision_note = "auto_resumed_after_stale_requested_handoff"
        request_row.lock_version += 1
        request_row.updated_at = now

    request_id = conversation.current_handoff_request_id
    conversation.current_handoff_request_id = None
    conversation.handoff_status = "none"
    conversation.active_agent_id = None
    conversation.ai_suspended = False
    conversation.ai_suspended_at = None
    conversation.ai_suspended_by = None
    conversation.ai_suspended_reason = None
    conversation.takeover_mode = None
    conversation.last_handoff_reason = None
    conversation.updated_at = now
    ticket.required_action = None
    ticket.conversation_state = ConversationState.ai_active
    ticket.updated_at = now

    event_payload = {
        "handoff_request_id": request_id,
        "message_id": visitor_message.id,
        "reason": "stale_requested_handoff",
        "stale_after_seconds": int(
            STALE_PUBLIC_HANDOFF_RESUME_AFTER.total_seconds()
        ),
    }
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="ai.resumed",
        payload=event_payload,
    )
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.conversation_state_changed,
            note="Stale WebChat handoff resumed by AI",
            payload_json=json.dumps(
                {"public_conversation_id": conversation.public_id, **event_payload},
                ensure_ascii=False,
            ),
        )
    )
    db.flush()
    return True


def add_visitor_message(
    db: Session,
    public_id: str,
    visitor_token: str | None,
    body: str,
    request: Request,
    *,
    client_message_id: str | None = None,
) -> dict[str, Any]:
    conversation = get_authorized_webchat_conversation(
        db,
        public_id=public_id,
        visitor_token=visitor_token,
    )
    return add_visitor_message_to_conversation(
        db,
        conversation=conversation,
        body=body,
        client_message_id=client_message_id,
        message_type="text",
        origin=origin_from_request(request),
    )


def add_visitor_message_to_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    body: str,
    client_message_id: str | None = None,
    message_type: str = "text",
    origin: str | None = None,
) -> dict[str, Any]:
    normalized_body = clip_body(body)
    normalized_client_id = clip(client_message_id, 120)
    if normalized_client_id:
        existing = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.client_message_id == normalized_client_id,
                WebchatMessage.direction == "visitor",
            )
            .first()
        )
        if existing is not None:
            return {
                "ok": True,
                "idempotent": True,
                "message": message_payload(existing),
            }

    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction="visitor",
        body=normalized_body,
        body_text=normalized_body,
        message_type=clip(message_type, 32) or "text",
        client_message_id=normalized_client_id,
        delivery_status="sent",
        metadata_json=_metadata(
            generated_by="visitor",
            origin=clip(origin, 255),
            fact_evidence_present=False,
        ),
        author_label=conversation.visitor_name or "Visitor",
    )
    db.add(message)
    db.flush()

    ticket = db.get(Ticket, conversation.ticket_id) if conversation.ticket_id else None
    if ticket is not None:
        ticket.last_customer_message = normalized_body
        ticket.customer_request = normalized_body
        ticket.updated_at = utc_now()
        if ticket.status in {TicketStatus.resolved, TicketStatus.closed}:
            ticket.status = TicketStatus.pending_assignment
            ticket.conversation_state = ConversationState.reopened_by_customer
        elif (
            ticket.conversation_state != ConversationState.human_review_required
            and not is_ai_suspended_for_handoff(conversation)
        ):
            ticket.conversation_state = ConversationState.human_owned
        db.add(
            TicketComment(
                ticket_id=ticket.id,
                author_id=None,
                body=normalized_body,
                visibility=NoteVisibility.external,
            )
        )
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.comment_added,
                note="Webchat visitor message received",
                payload_json=json.dumps(
                    {
                        "public_conversation_id": conversation.public_id,
                        "webchat_message_id": message.id,
                        "client_message_id": normalized_client_id,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    conversation.last_seen_at = utc_now()
    conversation.updated_at = utc_now()
    db.flush()
    _resume_stale_requested_handoff(
        db,
        conversation=conversation,
        ticket=ticket,
        visitor_message=message,
    )
    if is_ai_suspended_for_handoff(conversation):
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            event_type="ai_turn.suppressed_by_handoff",
            payload={
                "message_id": message.id,
                "reason": conversation.ai_suspended_reason,
            },
        )
    LOGGER.info(
        "webchat_message_received",
        extra={
            "event_payload": {
                "conversation_id": conversation.id,
                "ticket_id": conversation.ticket_id,
                "message_id": message.id,
            }
        },
    )
    db.refresh(message)
    return {"ok": True, "message": message_payload(message)}


def submit_card_action(
    db: Session,
    public_id: str,
    visitor_token: str | None,
    payload: WebChatActionSubmitRequest,
    request: Request,
) -> dict[str, Any]:
    conversation = get_authorized_webchat_conversation(
        db,
        public_id=public_id,
        visitor_token=visitor_token,
    )
    card_message = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.id == payload.message_id,
            WebchatMessage.conversation_id == conversation.id,
        )
        .first()
    )
    if card_message is None or (card_message.message_type or "text") != "card":
        raise HTTPException(status_code=404, detail="webchat card message not found")

    raw_card_payload = _loads_json(card_message.payload_json)
    if isinstance(raw_card_payload, str):
        raw_card_payload = _loads_json(raw_card_payload)
    raw_card_type = (
        raw_card_payload.get("card_type")
        if isinstance(raw_card_payload, dict)
        else None
    )
    if (
        raw_card_type == RETIRED_WEBCHAT_CARD_TYPE
        or payload.action_type == RETIRED_WEBCHAT_ACTION_TYPE
    ):
        raise HTTPException(status_code=410, detail="webchat retired card action")
    try:
        card_payload = WebChatCardPayload.model_validate(raw_card_payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid stored card payload") from exc
    if card_payload.card_id != payload.card_id:
        raise HTTPException(status_code=400, detail="card_id does not match message payload")
    selected = next(
        (item for item in card_payload.actions if item.id == payload.action_id),
        None,
    )
    if selected is None:
        raise HTTPException(status_code=400, detail="action_id is not allowed for this card")
    if selected.action_type != payload.action_type:
        raise HTTPException(
            status_code=400,
            detail="action_type does not match card action",
        )

    ticket = db.get(Ticket, conversation.ticket_id) if conversation.ticket_id else None
    action_payload = {
        "card_id": payload.card_id,
        "card_type": card_payload.card_type,
        "action_id": payload.action_id,
        "action_type": payload.action_type,
        "label": selected.label,
        "value": selected.value,
        "payload": payload.payload or selected.payload,
    }
    action = WebchatCardAction(
        conversation_id=conversation.id,
        ticket_id=ticket.id if ticket else None,
        message_id=card_message.id,
        action_type=payload.action_type,
        action_payload_json=json.dumps(action_payload, ensure_ascii=False),
        submitted_by="visitor",
        status="submitted",
        ip_hash=hash_optional(request.client.host if request.client else None),
        user_agent_hash=hash_optional(request.headers.get("user-agent")),
        origin=origin_from_request(request),
    )
    db.add(action)
    db.flush()

    action_text = f"Visitor selected: {selected.label}"
    action_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id if ticket else None,
        direction="action",
        body=action_text,
        body_text=action_text,
        message_type="action",
        payload_json=json.dumps(action_payload, ensure_ascii=False),
        metadata_json=_metadata(
            generated_by="visitor",
            action_row_id=action.id,
            fact_evidence_present=False,
        ),
        delivery_status="sent",
        action_status="submitted",
        author_label=conversation.visitor_name or "Visitor",
    )
    db.add(action_message)
    db.flush()
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id if ticket else None,
        event_type="message.created",
        payload={
            "message_id": action_message.id,
            "direction": "action",
            "message_type": "action",
        },
    )
    card_message.action_status = "submitted"

    if ticket is not None:
        db.add(
            TicketComment(
                ticket_id=ticket.id,
                author_id=None,
                body=action_text,
                visibility=NoteVisibility.external,
            )
        )
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.comment_added,
                note="Webchat card action submitted",
                payload_json=json.dumps(
                    {
                        "public_conversation_id": conversation.public_id,
                        "webchat_card_action_id": action.id,
                        "external_send": False,
                        **action_payload,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    handoff_triggered = (
        payload.action_type == "handoff_request"
        or card_payload.card_type == "handoff"
        or payload.action_id == "talk_to_human"
    )
    if handoff_triggered:
        if ticket is not None:
            ticket.required_action = "WebChat customer requested human support"
            ticket.status = TicketStatus.in_progress
            ticket.conversation_state = ConversationState.human_review_required
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="customer_action",
            trigger_type="card_action",
            reason_code="customer_requested_human_support",
            reason_text=selected.label,
            recommended_agent_action=(
                "Customer requested human support from the WebChat handoff card."
            ),
            trigger_message_id=action_message.id,
            requested_by_actor_type="visitor",
        )
        if ticket is not None:
            db.add(
                TicketEvent(
                    ticket_id=ticket.id,
                    actor_id=None,
                    event_type=EventType.conversation_state_changed,
                    note="Webchat handoff requested",
                    payload_json=json.dumps(
                        {
                            "public_conversation_id": conversation.public_id,
                            "required_action": ticket.required_action,
                            "external_send": False,
                        },
                        ensure_ascii=False,
                    ),
                )
            )

    now = utc_now()
    conversation.updated_at = now
    conversation.last_seen_at = now
    if ticket is not None:
        ticket.updated_at = now
    db.flush()
    db.refresh(action_message)
    return {
        "ok": True,
        "action_id": action.id,
        "status": action.status,
        "message": message_payload(action_message),
        "handoff_triggered": handoff_triggered,
    }
