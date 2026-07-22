from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..enums import (
    ConversationState,
    EventType,
    MessageStatus,
    SourceChannel,
    TicketStatus,
)
from ..models import Ticket, User
from ..models_agent_routing import ConversationControl
from ..operator_models import OperatorQueueScopeGrant
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatAITurn,
    WebchatCardAction,
    WebchatConversation,
    WebchatEvent,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .customer_visible_message_service import create_customer_visible_message
from .customer_visible_policy import (
    evaluate_customer_visible_policy,
    format_policy_reasons,
)
from .permissions import (
    CAP_OUTBOUND_SEND,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    ensure_can_send_outbound,
    ensure_ticket_visible,
    resolve_capabilities,
)
from .server_fact_evidence import resolve_server_fact_evidence
from .sla_service import evaluate_sla, update_first_response
from .ticket_service import get_ticket_or_404
from .ticketless_handoff_policy import can_resume_ticketless_handoff
from .webchat_ai_turn_service import ai_snapshot, safe_write_webchat_event
from .webchat_handoff_service import (
    ensure_can_reply_in_handoff,
    serialize_handoff_request,
)
from .webchat_inbox_read_state import webchat_read_state_payload
from .webchat_message_service import message_payload
from .webchat_session_identity import clip_body

MAX_THREAD_MESSAGES = 200
MAX_THREAD_ACTIONS = 50
MAX_THREAD_AI_TURNS = 20
MAX_THREAD_EVENTS = 30
SENSITIVE_EVENT_KEYS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "session_key",
)


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _redact_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if any(
                    marker in str(key).lower().replace("-", "_")
                    for marker in SENSITIVE_EVENT_KEYS
                )
                else _redact_event_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_event_payload(item) for item in value]
    return value


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
    if conversation.ticket_id is not None:
        ticket = get_ticket_or_404(db, conversation.ticket_id)
        ensure_ticket_visible(user, ticket, db)
        row = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .first()
        )
        if row is not None:
            return row
        return ConversationControl(
            conversation_id=conversation.id,
            customer_id=ticket.customer_id,
            tenant_key=conversation.tenant_key,
            country_code=ticket.country_code,
            channel_key=conversation.channel_key,
        )

    control = _control(db, conversation=conversation)
    if not control.country_code:
        raise HTTPException(
            status_code=403,
            detail="conversation_scope_unavailable",
        )
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
        raise HTTPException(
            status_code=403,
            detail="conversation_scope_not_authorized",
        )
    return control


def _handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> WebchatHandoffRequest | None:
    if conversation.current_handoff_request_id:
        row = db.get(
            WebchatHandoffRequest,
            conversation.current_handoff_request_id,
        )
        if row is not None:
            return row
    return (
        db.query(WebchatHandoffRequest)
        .filter(WebchatHandoffRequest.conversation_id == conversation.id)
        .order_by(WebchatHandoffRequest.id.desc())
        .first()
    )


def _handoff_payload(
    *,
    handoff: WebchatHandoffRequest,
    conversation: WebchatConversation,
    user: User,
    capabilities: set[str],
) -> dict[str, Any]:
    assigned_to_current_user = handoff.assigned_agent_id == user.id
    can_resume_ai = can_resume_ticketless_handoff(
        handoff=handoff,
        conversation=conversation,
        user_id=user.id,
        capabilities=capabilities,
    )
    return {
        "id": handoff.id,
        "conversation_id": conversation.public_id,
        "webchat_conversation_id": conversation.id,
        "ticket_id": None,
        "ticket_no": None,
        "title": (
            handoff.reason_text
            or handoff.reason_code
            or "WebChat human support"
        ),
        "status": handoff.status,
        "source": handoff.source,
        "trigger_type": handoff.trigger_type,
        "reason_code": handoff.reason_code,
        "reason_text": handoff.reason_text,
        "recommended_agent_action": handoff.recommended_agent_action,
        "assigned_agent_id": handoff.assigned_agent_id,
        "accepted_by_user_id": handoff.accepted_by_user_id,
        "requested_at": (
            handoff.requested_at.isoformat() if handoff.requested_at else None
        ),
        "accepted_at": (
            handoff.accepted_at.isoformat() if handoff.accepted_at else None
        ),
        "closed_at": (
            handoff.closed_at.isoformat() if handoff.closed_at else None
        ),
        "can_accept": (
            handoff.status == "requested"
            and CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities
        ),
        "can_decline": (
            handoff.status == "requested"
            and CAP_WEBCHAT_HANDOFF_DECLINE in capabilities
        ),
        "can_force_takeover": False,
        "can_release": (
            handoff.status == "accepted"
            and assigned_to_current_user
            and CAP_WEBCHAT_HANDOFF_RELEASE in capabilities
        ),
        "can_resume_ai": can_resume_ai,
        "can_reply": (
            handoff.status == "accepted"
            and assigned_to_current_user
            and CAP_OUTBOUND_SEND in capabilities
        ),
    }


def _ai_turn_payload(row: WebchatAITurn) -> dict[str, Any]:
    trace = _loads_json(row.runtime_trace_json)
    return {
        "id": row.id,
        "status": row.status,
        "trigger_message_id": row.trigger_message_id,
        "latest_visitor_message_id": row.latest_visitor_message_id,
        "context_cutoff_message_id": row.context_cutoff_message_id,
        "reply_message_id": row.reply_message_id,
        "reply_source": row.reply_source,
        "fallback_reason": row.fallback_reason,
        "bridge_elapsed_ms": row.bridge_elapsed_ms,
        "runtime_trace": trace if isinstance(trace, dict) else None,
    }


def _event_payload(row: WebchatEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "payload_json": _redact_event_payload(
            _loads_json(row.payload_json) or {}
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
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
        query = query.filter(
            WebchatMessage.id < max(1, int(before_message_id))
        )
    fetched = (
        query.order_by(WebchatMessage.id.desc()).limit(safe_limit + 1).all()
    )
    has_more = len(fetched) > safe_limit
    messages = list(reversed(fetched[:safe_limit]))
    next_before_id = messages[0].id if has_more and messages else None
    handoff = _handoff(db, conversation=conversation)
    ticket = (
        db.get(Ticket, conversation.ticket_id)
        if conversation.ticket_id
        else None
    )

    payload: dict[str, Any] = {
        "conversation_id": conversation.public_id,
        "ticket_id": ticket.id if ticket else None,
        "ticket_no": ticket.ticket_no if ticket else None,
        "origin": conversation.origin,
        "page_url": conversation.page_url,
        "status": (
            ticket.status.value
            if ticket and hasattr(ticket.status, "value")
            else str(ticket.status)
            if ticket
            else conversation.status
        ),
        "conversation_state": (
            ticket.conversation_state.value
            if ticket and hasattr(ticket.conversation_state, "value")
            else str(ticket.conversation_state)
            if ticket
            else "human_owned"
            if conversation.handoff_status == "accepted"
            else "human_review_required"
            if conversation.handoff_status == "requested"
            else "ai_active"
            if conversation.status == "open"
            else "closed"
        ),
        "required_action": (
            ticket.required_action
            if ticket
            else (
                handoff.recommended_agent_action
                or handoff.reason_text
                or handoff.reason_code
            )
            if handoff
            else None
        ),
        "outcome": control.outcome,
        "visitor": {
            "name": conversation.visitor_name,
            "email": conversation.visitor_email,
            "phone": conversation.visitor_phone,
            "ref": conversation.visitor_ref,
        },
        "messages": [message_payload(row) for row in messages],
        "message_page": {
            "before_id": next_before_id,
            "has_more": has_more,
            "limit": safe_limit,
        },
    }
    if ticket is None:
        payload["handoff"] = (
            _handoff_payload(
                handoff=handoff,
                conversation=conversation,
                user=user,
                capabilities=capabilities,
            )
            if handoff
            else None
        )
        payload.update(ai_snapshot(conversation))
        return payload

    action_rows = (
        db.query(WebchatCardAction)
        .filter(WebchatCardAction.conversation_id == conversation.id)
        .order_by(WebchatCardAction.id.desc())
        .limit(MAX_THREAD_ACTIONS)
        .all()
    )
    action_rows.reverse()
    ai_turn_rows = (
        db.query(WebchatAITurn)
        .filter(WebchatAITurn.conversation_id == conversation.id)
        .order_by(WebchatAITurn.id.desc())
        .limit(MAX_THREAD_AI_TURNS)
        .all()
    )
    event_rows = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == conversation.id)
        .order_by(WebchatEvent.id.desc())
        .limit(MAX_THREAD_EVENTS)
        .all()
    )
    payload.update(
        {
            "handoff": (
                serialize_handoff_request(
                    db,
                    handoff,
                    current_user=user,
                    conversation=conversation,
                    ticket=ticket,
                )
                if handoff
                else None
            ),
            "actions": [
                {
                    "id": action.id,
                    "message_id": action.message_id,
                    "action_type": action.action_type,
                    "status": action.status,
                    "payload": _loads_json(action.action_payload_json) or {},
                    "submitted_by": action.submitted_by,
                    "origin": action.origin,
                    "created_at": (
                        action.created_at.isoformat()
                        if action.created_at
                        else None
                    ),
                }
                for action in action_rows
            ],
            "ai_turns": [
                _ai_turn_payload(row) for row in reversed(ai_turn_rows)
            ],
            "events": [_event_payload(row) for row in reversed(event_rows)],
            **webchat_read_state_payload(
                db,
                conversation_id=conversation.id,
                user_id=user.id,
            ),
        }
    )
    return payload


def _reply_channel(
    ticket: Ticket,
    conversation: WebchatConversation,
) -> SourceChannel:
    values = {
        str(getattr(value, "value", value) or "").strip().lower()
        for value in (
            ticket.preferred_reply_channel,
            ticket.source_channel,
            conversation.channel_key,
        )
    }
    return (
        SourceChannel.whatsapp
        if SourceChannel.whatsapp.value in values
        else SourceChannel.web_chat
    )


def _reply_ticket_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    user: User,
    body: str,
    evidence_reference_id: int | None,
) -> dict[str, Any]:
    ensure_ticket_visible(user, ticket, db)
    ensure_can_reply_in_handoff(
        db,
        conversation=conversation,
        ticket=ticket,
        current_user=user,
    )
    text = clip_body(body)
    evidence = resolve_server_fact_evidence(
        db,
        ticket=ticket,
        conversation=conversation,
        evidence_reference_id=evidence_reference_id,
    )
    decision = evaluate_customer_visible_policy(text)
    decision_payload = asdict(decision)
    decision_payload["evidence"] = evidence.audit_payload()
    if not decision.allowed:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Outbound reply blocked by customer-visible content policy"
                ),
                "policy": decision_payload,
            },
        )

    if ticket.conversation_state == ConversationState.ai_active:
        ticket.conversation_state = ConversationState.human_owned
        ticket.required_action = None
        conversation.handoff_status = "accepted"
        conversation.accepted_by_user_id = user.id
        conversation.accepted_at = utc_now()

    channel = _reply_channel(ticket, conversation)
    external = channel == SourceChannel.whatsapp
    provider_status = (
        "whatsapp_agent_reply_queued" if external else "webchat_delivered"
    )
    result = create_customer_visible_message(
        db,
        ticket=ticket,
        conversation=conversation,
        channel=channel,
        body=decision.normalized_body,
        origin="human_agent",
        created_by=user.id,
        provider_status=provider_status,
        outbound_status=None if external else MessageStatus.sent,
        delivery_status="queued" if external else "sent",
        metadata_json={
            "generated_by": "human_agent",
            "safety_level": decision.level,
            "fact_evidence_present": evidence.present,
            "fact_evidence_reference_id": evidence.reference_id,
            "fact_evidence_reason": evidence.reason,
            "external_send": external,
            "reply_channel": channel.value,
        },
        author_label=user.display_name,
        author_user_id=user.id,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(
            decision.reasons,
            ensure_ascii=False,
        ),
        comment_author_id=user.id,
        event_type=(
            EventType.outbound_queued if external else EventType.outbound_sent
        ),
        event_note=(
            "WhatsApp agent reply queued"
            if external
            else "Webchat agent reply sent"
        ),
        event_payload={
            "public_conversation_id": conversation.public_id,
            "safety_level": decision.level,
            "safety_reasons": decision.reasons,
            "safety_reason_text": format_policy_reasons(decision),
            "external_send": external,
            "reply_channel": channel.value,
            "provider_status": provider_status,
            "case_context_id": evidence.reference_id,
            "fact_evidence_present": evidence.present,
            "fact_evidence_reason": evidence.reason,
        },
    )
    if result.webchat_message is None or result.outbound_message is None:
        raise HTTPException(
            status_code=500,
            detail="customer visible reply was not created",
        )
    metadata = _loads_json(result.webchat_message.metadata_json) or {}
    metadata.update(
        {
            "outbound_message_id": result.outbound_message.id,
            "provider_status": provider_status,
        }
    )
    result.webchat_message.metadata_json = json.dumps(
        metadata,
        ensure_ascii=False,
        default=str,
    )
    update_first_response(ticket)
    ticket.status = TicketStatus.waiting_customer
    ticket.conversation_state = ConversationState.waiting_customer
    ticket.last_human_update = decision.normalized_body
    ticket.updated_at = utc_now()
    conversation.updated_at = utc_now()
    db.flush()
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload={
            "message_id": result.webchat_message.id,
            "direction": "agent",
            "author_user_id": user.id,
        },
    )
    evaluate_sla(ticket, db)
    db.flush()
    db.refresh(result.webchat_message)
    return {
        "ok": True,
        "safety": decision_payload,
        "message": message_payload(result.webchat_message),
    }


def _reply_ticketless_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
    body: str,
) -> dict[str, Any]:
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

    decision = evaluate_customer_visible_policy(clip_body(body))
    decision_payload = asdict(decision)
    if not decision.allowed:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Outbound reply blocked by customer-visible content policy"
                ),
                "policy": decision_payload,
            },
        )
    result = create_customer_visible_message(
        db,
        ticket=None,
        conversation=conversation,
        channel=SourceChannel.web_chat,
        body=decision.normalized_body,
        origin="human_agent",
        created_by=user.id,
        provider_status="webchat_delivered",
        outbound_status=MessageStatus.sent,
        delivery_status="sent",
        metadata_json={
            "generated_by": "human_agent",
            "safety_level": decision.level,
            "external_send": False,
            "reply_channel": SourceChannel.web_chat.value,
        },
        author_label=user.display_name,
        author_user_id=user.id,
        safety_level=decision.level,
        safety_reasons_json=json.dumps(
            decision.reasons,
            ensure_ascii=False,
        ),
        create_external_comment=False,
    )
    if result.webchat_message is None or result.outbound_message is not None:
        raise HTTPException(
            status_code=500,
            detail="ticketless customer visible reply was not created",
        )
    conversation.updated_at = utc_now()
    conversation.last_seen_at = utc_now()
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=None,
        event_type="message.created",
        payload={
            "message_id": result.webchat_message.id,
            "direction": "agent",
            "author_user_id": user.id,
        },
    )
    db.flush()
    db.refresh(result.webchat_message)
    return {
        "ok": True,
        "safety": decision_payload,
        "message": message_payload(result.webchat_message),
    }


def reply_to_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
    body: str,
    evidence_reference_id: int | None = None,
) -> dict[str, Any]:
    ensure_can_send_outbound(user, db)
    ensure_conversation_visible(
        db,
        conversation=conversation,
        user=user,
    )
    ticket = (
        db.get(Ticket, conversation.ticket_id)
        if conversation.ticket_id
        else None
    )
    if ticket is not None:
        return _reply_ticket_conversation(
            db,
            conversation=conversation,
            ticket=ticket,
            user=user,
            body=body,
            evidence_reference_id=evidence_reference_id,
        )
    return _reply_ticketless_conversation(
        db,
        conversation=conversation,
        user=user,
        body=body,
    )


def read_ticket_conversation_thread(
    db: Session,
    ticket_id: int,
    user: User,
    *,
    before_message_id: int | None = None,
    message_limit: int = 100,
) -> dict[str, Any]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(user, ticket, db)
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.ticket_id == ticket.id)
        .first()
    )
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="webchat conversation not found for ticket",
        )
    return read_conversation_thread(
        db,
        conversation=conversation,
        user=user,
        before_message_id=before_message_id,
        message_limit=message_limit,
    )


def reply_to_ticket_conversation(
    db: Session,
    ticket_id: int,
    user: User,
    *,
    body: str,
    evidence_reference_id: int | None = None,
    conversation_public_id: str | None = None,
) -> dict[str, Any]:
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(user, ticket, db)
    query = db.query(WebchatConversation).filter(
        WebchatConversation.ticket_id == ticket.id
    )
    if conversation_public_id:
        query = query.filter(
            WebchatConversation.public_id == conversation_public_id
        )
    conversation = query.first()
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="webchat conversation not found for ticket",
        )
    return reply_to_conversation(
        db,
        conversation=conversation,
        user=user,
        body=body,
        evidence_reference_id=evidence_reference_id,
    )
