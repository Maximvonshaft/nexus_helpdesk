from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import User
from ..models_agent_routing import ConversationControl
from ..operator_models import OperatorQueueScopeGrant
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatConversation,
    WebchatEvent,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .permissions import (
    CAP_OUTBOUND_SEND,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    ensure_can_send_outbound,
    resolve_capabilities,
)
from .webchat_ai_turn_service import ai_snapshot


MAX_THREAD_MESSAGES = 200


def _control(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> ConversationControl:
    row = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=409, detail="conversation_control_missing")
    return row


def ensure_conversation_visible(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
) -> ConversationControl:
    control = _control(db, conversation=conversation)
    if not control.country_code:
        raise HTTPException(status_code=403, detail="conversation_scope_unavailable")
    grant = (
        db.query(OperatorQueueScopeGrant.id)
        .filter(
            OperatorQueueScopeGrant.user_id == user.id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )
    if grant is None:
        raise HTTPException(status_code=403, detail="conversation_scope_not_authorized")
    return control


def _handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> WebchatHandoffRequest | None:
    if conversation.current_handoff_request_id:
        row = db.get(WebchatHandoffRequest, conversation.current_handoff_request_id)
        if row is not None:
            return row
    return (
        db.query(WebchatHandoffRequest)
        .filter(WebchatHandoffRequest.conversation_id == conversation.id)
        .order_by(WebchatHandoffRequest.id.desc())
        .first()
    )


def _message_payload(row: WebchatMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body_text or row.body,
        "body_text": row.body_text or row.body,
        "message_type": row.message_type,
        "delivery_status": row.delivery_status,
        "author_label": row.author_label,
        "author_user_id": row.author_user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _handoff_payload(
    *,
    handoff: WebchatHandoffRequest,
    conversation: WebchatConversation,
    user: User,
    capabilities: set[str],
) -> dict[str, Any]:
    assigned_to_current_user = handoff.assigned_agent_id == user.id
    return {
        "id": handoff.id,
        "conversation_id": conversation.public_id,
        "webchat_conversation_id": conversation.id,
        "ticket_id": None,
        "ticket_no": None,
        "title": handoff.reason_text or handoff.reason_code or "WebChat human support",
        "status": handoff.status,
        "source": handoff.source,
        "trigger_type": handoff.trigger_type,
        "reason_code": handoff.reason_code,
        "reason_text": handoff.reason_text,
        "recommended_agent_action": handoff.recommended_agent_action,
        "assigned_agent_id": handoff.assigned_agent_id,
        "accepted_by_user_id": handoff.accepted_by_user_id,
        "requested_at": handoff.requested_at.isoformat()
        if handoff.requested_at
        else None,
        "accepted_at": handoff.accepted_at.isoformat()
        if handoff.accepted_at
        else None,
        "closed_at": handoff.closed_at.isoformat() if handoff.closed_at else None,
        "can_accept": handoff.status == "requested"
        and CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities,
        "can_decline": handoff.status == "requested"
        and CAP_WEBCHAT_HANDOFF_DECLINE in capabilities,
        "can_force_takeover": False,
        "can_release": handoff.status == "accepted"
        and assigned_to_current_user
        and CAP_WEBCHAT_HANDOFF_RELEASE in capabilities,
        "can_resume_ai": handoff.status in {"requested", "accepted"}
        and CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities,
        "can_reply": handoff.status == "accepted"
        and assigned_to_current_user
        and CAP_OUTBOUND_SEND in capabilities,
    }


def read_conversation_thread(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
    before_message_id: int | None = None,
    message_limit: int = 100,
) -> dict[str, Any]:
    control = ensure_conversation_visible(
        db,
        conversation=conversation,
        user=user,
    )
    capabilities = resolve_capabilities(user, db)
    safe_limit = max(1, min(int(message_limit or 100), MAX_THREAD_MESSAGES))
    query = db.query(WebchatMessage).filter(
        WebchatMessage.conversation_id == conversation.id
    )
    if before_message_id is not None:
        query = query.filter(WebchatMessage.id < int(before_message_id))
    rows = query.order_by(WebchatMessage.id.desc()).limit(safe_limit + 1).all()
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    rows.reverse()
    handoff = _handoff(db, conversation=conversation)
    conversation_state = (
        "human_owned"
        if conversation.handoff_status == "accepted"
        else "human_review_required"
        if conversation.handoff_status == "requested"
        else "ai_active"
        if conversation.status == "open"
        else "closed"
    )
    return {
        "conversation_id": conversation.public_id,
        "ticket_id": None,
        "ticket_no": None,
        "origin": conversation.origin,
        "page_url": conversation.page_url,
        "status": conversation.status,
        "conversation_state": conversation_state,
        "required_action": (
            handoff.recommended_agent_action
            or handoff.reason_text
            or handoff.reason_code
            if handoff is not None
            else None
        ),
        "outcome": control.outcome,
        "visitor": {
            "name": conversation.visitor_name,
            "email": conversation.visitor_email,
            "phone": conversation.visitor_phone,
            "ref": conversation.visitor_ref,
        },
        "messages": [_message_payload(row) for row in rows],
        "handoff": (
            _handoff_payload(
                handoff=handoff,
                conversation=conversation,
                user=user,
                capabilities=capabilities,
            )
            if handoff is not None
            else None
        ),
        "message_page": {
            "before_id": rows[0].id if rows and has_more else None,
            "has_more": has_more,
            "limit": safe_limit,
        },
        **ai_snapshot(conversation),
    }


def reply_to_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
    body: str,
) -> dict[str, Any]:
    ensure_can_send_outbound(user, db)
    ensure_conversation_visible(db, conversation=conversation, user=user)
    if conversation.ticket_id is not None:
        raise HTTPException(
            status_code=409,
            detail="ticket_backed_conversation_uses_ticket_reply_authority",
        )
    if conversation.status != "open":
        raise HTTPException(status_code=409, detail="conversation_is_closed")
    handoff = _handoff(db, conversation=conversation)
    if (
        handoff is None
        or handoff.status != "accepted"
        or handoff.assigned_agent_id != user.id
        or conversation.active_agent_id != user.id
    ):
        raise HTTPException(
            status_code=409,
            detail="handoff_must_be_accepted_before_replying",
        )
    text = " ".join(str(body or "").strip().split())
    if not text:
        raise HTTPException(status_code=400, detail="reply_body_required")
    now = utc_now()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="agent",
        body=text[:2000],
        body_text=text[:2000],
        message_type="text",
        author_user_id=user.id,
        author_label=user.display_name,
        delivery_status="sent",
        created_at=now,
    )
    db.add(message)
    db.flush()
    db.add(
        WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=None,
            event_type="message.created",
            payload_json=json.dumps(
                {
                    "message_id": message.id,
                    "direction": "agent",
                    "actor_id": user.id,
                },
                ensure_ascii=False,
            ),
            created_at=now,
        )
    )
    conversation.updated_at = now
    conversation.last_seen_at = now
    db.flush()
    return {
        "ok": True,
        "conversation_id": conversation.public_id,
        "message": _message_payload(message),
    }
