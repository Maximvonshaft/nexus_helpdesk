"""Canonical WebChat Handoff authority.

Ticket-backed and ticketless conversations share one HandoffRequest, one routing
policy, one OperatorTask projection and one command surface. Ticket is an
optional Case association, never a switch to a parallel state machine.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, TicketStatus
from ..models import Ticket, TicketEvent, User
from ..models_agent_routing import ConversationControl
from ..operator_models import OperatorQueueScopeGrant, OperatorTask
from ..utils.time import ensure_utc, utc_now
from ..voice_models import VoiceRoutingOffer, WebchatVoiceSession
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .agent_routing_service import (
    VOICE_OPEN_SESSION_STATUSES,
    assign_handoff_to_agent,
    create_next_voice_offer,
    decline_voice_offer,
    fill_agent_capacity,
    request_handoff,
)
from .audit_service import log_admin_audit
from .handoff_responsibility_policy import can_resume_handoff
from .handoff_routing_policy import (
    HandoffRoutingPolicyError,
    active_decline_exists,
    mark_routing_outcome,
    record_routing_decline,
    request_policy,
    routing_projection,
    start_next_routing_generation,
    user_is_routing_eligible,
)
from .operator_queue import HANDOFF_PROJECTION_SOURCE
from .permissions import (
    CAP_OUTBOUND_SEND,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    has_global_case_visibility,
    resolve_capabilities,
)
from .webchat_ai_turn_service import (
    ai_snapshot,
    safe_write_webchat_event,
)
from .webchat_inbox_read_state import (
    webchat_read_state_payload,
)

OPEN_HANDOFF_STATUSES = {"requested", "accepted"}
TERMINAL_HANDOFF_STATUSES = {"closed", "cancelled", "expired", "resumed_ai"}
AI_ACTIVE_STATUSES = {
    "queued",
    "processing",
    "bridge_calling",
    "fallback_generating",
}
MAX_NOTE_CHARS = 1000


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    return cleaned[:limit] if cleaned else None


def _lock(query, db: Session):
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        return query.with_for_update()
    return query


def _request_by_id(
    db: Session,
    request_id: int,
    *,
    lock: bool = False,
) -> WebchatHandoffRequest:
    query = db.query(WebchatHandoffRequest).filter(
        WebchatHandoffRequest.id == request_id
    )
    if lock:
        query = _lock(query, db)
    row = query.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    return row


def _context(
    db: Session,
    request_row: WebchatHandoffRequest,
) -> tuple[WebchatConversation, ConversationControl, Ticket | None]:
    conversation = db.get(WebchatConversation, request_row.conversation_id)
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == request_row.conversation_id)
        .first()
    )
    ticket = (
        db.get(Ticket, request_row.ticket_id)
        if request_row.ticket_id is not None
        else None
    )
    if conversation is None or control is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff source is missing",
        )
    if request_row.ticket_id is not None and ticket is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff ticket is missing",
        )
    return conversation, control, ticket


def _scope_visible(
    db: Session,
    *,
    current_user: User,
    control: ConversationControl,
) -> bool:
    if not control.country_code:
        return False
    return bool(
        db.query(OperatorQueueScopeGrant.id)
        .filter(
            OperatorQueueScopeGrant.user_id == current_user.id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def _require_scope(
    db: Session,
    *,
    current_user: User,
    control: ConversationControl,
) -> None:
    if not _scope_visible(
        db,
        current_user=current_user,
        control=control,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="conversation_scope_not_authorized",
        )


def _require_capability(
    db: Session,
    *,
    current_user: User,
    capability: str,
    detail: str,
) -> set[str]:
    capabilities = resolve_capabilities(current_user, db)
    if capability not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )
    return capabilities


def _last_message(db: Session, conversation_id: int) -> WebchatMessage | None:
    return (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation_id)
        .order_by(WebchatMessage.id.desc())
        .first()
    )


def _serialize_last_message(row: WebchatMessage | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "direction": row.direction,
        "body_text": row.body_text or row.body,
        "message_type": row.message_type,
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _operator_task(
    db: Session,
    request_id: int,
) -> OperatorTask | None:
    return (
        db.query(OperatorTask)
        .filter(
            OperatorTask.source_type == HANDOFF_PROJECTION_SOURCE,
            OperatorTask.source_id == str(request_id),
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(
                [
                    "resolved",
                    "dropped",
                    "replayed",
                    "replay_failed",
                    "cancelled",
                ]
            ),
        )
        .order_by(OperatorTask.id.desc())
        .first()
    )


def _write_event(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    request_row: WebchatHandoffRequest,
    event_type: str,
    actor_id: int | None,
    payload: dict[str, Any] | None = None,
) -> None:
    base = {
        "handoff_request_id": request_row.id,
        "status": request_row.status,
        "actor_id": actor_id,
        "routing": routing_projection(request_row),
        **(payload or {}),
    }
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id if ticket is not None else None,
        event_type=event_type,
        payload=base,
    )
    if ticket is not None:
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=actor_id,
                event_type=EventType.conversation_state_changed,
                note=event_type.replace(".", " "),
                payload_json=json.dumps(
                    {
                        "public_conversation_id": conversation.public_id,
                        **base,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                created_at=utc_now(),
            )
        )


def _waiting_seconds(request_row: WebchatHandoffRequest) -> int:
    requested = ensure_utc(request_row.requested_at)
    now = ensure_utc(utc_now())
    if requested is None or now is None:
        return 0
    return max(0, int((now - requested).total_seconds()))


def serialize_handoff_request(
    db: Session,
    request_row: WebchatHandoffRequest,
    *,
    current_user: User | None = None,
    conversation: WebchatConversation | None = None,
    ticket: Ticket | None = None,
) -> dict[str, Any]:
    if conversation is None:
        conversation = db.get(WebchatConversation, request_row.conversation_id)
    if ticket is None and request_row.ticket_id is not None:
        ticket = db.get(Ticket, request_row.ticket_id)
    capabilities = (
        resolve_capabilities(current_user, db) if current_user is not None else set()
    )
    declined_by_me = bool(
        current_user
        and active_decline_exists(
            db,
            request_row=request_row,
            user_id=current_user.id,
        )
    )
    routing_eligible = False
    if current_user is not None:
        try:
            routing_eligible = user_is_routing_eligible(
                db,
                user=current_user,
                request_row=request_row,
            )
        except HandoffRoutingPolicyError:
            routing_eligible = False
    can_accept = bool(
        current_user
        and request_row.status == "requested"
        and not declined_by_me
        and routing_eligible
        and CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities
    )
    can_reply = bool(
        current_user
        and request_row.status == "accepted"
        and request_row.assigned_agent_id == current_user.id
        and conversation is not None
        and conversation.active_agent_id == current_user.id
        and CAP_OUTBOUND_SEND in capabilities
    )
    can_resume = bool(
        current_user is not None
        and conversation is not None
        and can_resume_handoff(
            handoff=request_row,
            conversation=conversation,
            user_id=current_user.id,
            capabilities=capabilities,
        )
    )
    payload: dict[str, Any] = {
        "id": request_row.id,
        "conversation_id": conversation.public_id if conversation else None,
        "webchat_conversation_id": request_row.conversation_id,
        "ticket_id": request_row.ticket_id,
        "ticket_no": ticket.ticket_no if ticket else None,
        "title": (
            ticket.title
            if ticket is not None
            else request_row.reason_text
            or request_row.reason_code
            or "WebChat human support"
        ),
        "status": request_row.status,
        "source": request_row.source,
        "trigger_type": request_row.trigger_type,
        "reason_code": request_row.reason_code,
        "reason_text": request_row.reason_text,
        "recommended_agent_action": request_row.recommended_agent_action,
        "assigned_agent_id": request_row.assigned_agent_id,
        "accepted_by_user_id": request_row.accepted_by_user_id,
        "forced_by_user_id": request_row.forced_by_user_id,
        "declined_by_me": declined_by_me,
        "waiting_seconds": _waiting_seconds(request_row),
        "requested_at": (
            request_row.requested_at.isoformat()
            if request_row.requested_at
            else None
        ),
        "accepted_at": (
            request_row.accepted_at.isoformat()
            if request_row.accepted_at
            else None
        ),
        "released_at": (
            request_row.released_at.isoformat()
            if request_row.released_at
            else None
        ),
        "closed_at": (
            request_row.closed_at.isoformat()
            if request_row.closed_at
            else None
        ),
        "handoff_status": (
            conversation.handoff_status if conversation else request_row.status
        ),
        "active_agent_id": (
            conversation.active_agent_id
            if conversation
            else request_row.assigned_agent_id
        ),
        "ai_suspended": bool(conversation.ai_suspended) if conversation else False,
        "ai_status": conversation.active_ai_status if conversation else None,
        "ai_turn_id": (
            conversation.active_ai_turn_id
            if conversation
            else request_row.ai_turn_id
        ),
        "takeover_mode": conversation.takeover_mode if conversation else None,
        "visitor_name": conversation.visitor_name if conversation else None,
        "visitor_email": conversation.visitor_email if conversation else None,
        "visitor_phone": conversation.visitor_phone if conversation else None,
        "origin": conversation.origin if conversation else None,
        "last_message": _serialize_last_message(
            _last_message(db, request_row.conversation_id)
        ),
        "can_accept": can_accept,
        "can_decline": bool(
            current_user
            and request_row.status == "requested"
            and CAP_WEBCHAT_HANDOFF_DECLINE in capabilities
        ),
        "can_force_takeover": bool(
            CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities
        ),
        "can_release": bool(
            current_user
            and request_row.status == "accepted"
            and (
                request_row.assigned_agent_id == current_user.id
                or CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities
            )
            and CAP_WEBCHAT_HANDOFF_RELEASE in capabilities
        ),
        "can_resume_ai": can_resume,
        "can_reply": can_reply,
        "routing": routing_projection(request_row),
    }
    if conversation is not None:
        payload.update(ai_snapshot(conversation))
        if current_user is not None:
            payload.update(
                webchat_read_state_payload(
                    db,
                    conversation_id=conversation.id,
                    user_id=current_user.id,
                )
            )
    from .agent_availability_service import queue_position

    payload["queue_position"] = queue_position(
        db,
        request_row=request_row,
    )
    return payload


def request_webchat_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    source: str,
    trigger_type: str,
    reason_code: str | None = None,
    reason_text: str | None = None,
    recommended_agent_action: str | None = None,
    trigger_message_id: int | None = None,
    ai_turn_id: int | None = None,
    requested_by_actor_type: str = "system",
    requested_by_user_id: int | None = None,
    note: str | None = None,
) -> WebchatHandoffRequest:
    if ticket is not None and conversation.ticket_id != ticket.id:
        raise HTTPException(
            status_code=409,
            detail="webchat handoff ticket identity conflict",
        )
    row = request_handoff(
        db,
        conversation=conversation,
        source=source,
        trigger_type=trigger_type,
        reason_code=reason_code,
        reason_text=reason_text,
        recommended_agent_action=recommended_agent_action,
        trigger_message_id=trigger_message_id,
        ai_turn_id=ai_turn_id,
        requested_by_actor_type=requested_by_actor_type,
        requested_by_user_id=requested_by_user_id,
    )
    if ticket is not None:
        ticket.required_action = (
            _clip(recommended_agent_action, 1000)
            or ticket.required_action
            or row.reason_code
        )
        ticket.conversation_state = ConversationState.human_review_required
        if ticket.status in {
            TicketStatus.new,
            TicketStatus.resolved,
            TicketStatus.closed,
            TicketStatus.canceled,
        }:
            ticket.status = TicketStatus.pending_assignment
        ticket.updated_at = utc_now()
    if note:
        row.decision_note = _clip(note, MAX_NOTE_CHARS)
    db.flush()
    return row


def _visible_ai_active_items(
    db: Session,
    *,
    current_user: User,
    limit: int,
) -> list[dict[str, Any]]:
    capabilities = resolve_capabilities(current_user, db)
    rows = (
        db.query(WebchatConversation, ConversationControl, Ticket)
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == current_user.id,
                OperatorQueueScopeGrant.tenant_key
                == ConversationControl.tenant_key,
                OperatorQueueScopeGrant.country_code
                == ConversationControl.country_code,
                OperatorQueueScopeGrant.channel_key
                == ConversationControl.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .outerjoin(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .filter(
            WebchatConversation.status == "open",
            WebchatConversation.ai_suspended.is_(False),
            WebchatConversation.active_ai_status.in_(AI_ACTIVE_STATUSES),
            ConversationControl.country_code.is_not(None),
        )
        .order_by(
            WebchatConversation.active_ai_updated_at.desc(),
            WebchatConversation.updated_at.desc(),
        )
        .limit(limit)
        .all()
    )
    return [
        {
            "id": None,
            "conversation_id": conversation.public_id,
            "webchat_conversation_id": conversation.id,
            "ticket_id": ticket.id if ticket else None,
            "ticket_no": ticket.ticket_no if ticket else None,
            "title": ticket.title if ticket else "WebChat conversation",
            "status": "ai_active",
            "source": "ai_active",
            "trigger_type": "monitor_ai",
            "reason_code": conversation.active_ai_status,
            "reason_text": "AI is currently handling this conversation",
            "recommended_agent_action": (
                "Force takeover if the AI conversation needs human intervention."
            ),
            "assigned_agent_id": conversation.active_agent_id,
            "declined_by_me": False,
            "waiting_seconds": 0,
            "requested_at": None,
            "handoff_status": conversation.handoff_status,
            "active_agent_id": conversation.active_agent_id,
            "ai_suspended": bool(conversation.ai_suspended),
            "ai_status": conversation.active_ai_status,
            "ai_turn_id": conversation.active_ai_turn_id,
            "takeover_mode": conversation.takeover_mode,
            "visitor_name": conversation.visitor_name,
            "visitor_email": conversation.visitor_email,
            "visitor_phone": conversation.visitor_phone,
            "origin": conversation.origin,
            "last_message": _serialize_last_message(
                _last_message(db, conversation.id)
            ),
            "can_accept": False,
            "can_decline": False,
            "can_force_takeover": (
                CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities
            ),
            "can_release": False,
            "can_resume_ai": False,
            "can_reply": False,
            **webchat_read_state_payload(
                db,
                conversation_id=conversation.id,
                user_id=current_user.id,
            ),
            **ai_snapshot(conversation),
        }
        for conversation, _control, ticket in rows
    ]


def list_handoff_queue(
    db: Session,
    current_user: User,
    *,
    view: str = "requested",
    include_declined: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    capabilities = resolve_capabilities(current_user, db)
    permissions = {
        "can_accept": CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities,
        "can_decline": CAP_WEBCHAT_HANDOFF_DECLINE in capabilities,
        "can_force_takeover": CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities,
        "can_release": CAP_WEBCHAT_HANDOFF_RELEASE in capabilities,
        "can_resume_ai": CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities,
    }
    safe_limit = max(1, min(int(limit or 50), 100))
    if view == "ai_active":
        return {
            "items": _visible_ai_active_items(
                db,
                current_user=current_user,
                limit=safe_limit,
            ),
            "view": view,
            "permissions": permissions,
        }

    query = (
        db.query(
            WebchatHandoffRequest,
            WebchatConversation,
            ConversationControl,
            Ticket,
        )
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == current_user.id,
                OperatorQueueScopeGrant.tenant_key
                == ConversationControl.tenant_key,
                OperatorQueueScopeGrant.country_code
                == ConversationControl.country_code,
                OperatorQueueScopeGrant.channel_key
                == ConversationControl.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .outerjoin(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .filter(ConversationControl.country_code.is_not(None))
    )
    if view == "mine":
        query = query.filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == current_user.id,
        )
    elif view == "closed":
        query = query.filter(
            WebchatHandoffRequest.status.in_(TERMINAL_HANDOFF_STATUSES)
        )
    else:
        query = query.filter(WebchatHandoffRequest.status == "requested")
    rows = query.limit(safe_limit * 5).all()
    items: list[dict[str, Any]] = []
    for request_row, conversation, _control, ticket in rows:
        if (
            view == "requested"
            and not include_declined
            and active_decline_exists(
                db,
                request_row=request_row,
                user_id=current_user.id,
            )
        ):
            continue
        items.append(
            serialize_handoff_request(
                db,
                request_row,
                current_user=current_user,
                conversation=conversation,
                ticket=ticket,
            )
        )
    items.sort(
        key=lambda item: (
            int((item.get("routing") or {}).get("priority") or 100),
            str(item.get("requested_at") or ""),
            int(item.get("id") or 0),
        )
    )
    return {
        "items": items[:safe_limit],
        "view": view,
        "permissions": permissions,
    }


def accept_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict[str, Any]:
    _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_ACCEPT,
        detail="webchat_handoff_accept_requires_capability",
    )
    row = _request_by_id(db, request_id, lock=True)
    conversation, control, ticket = _context(db, row)
    _require_scope(db, current_user=current_user, control=control)
    if row.status == "accepted" and row.assigned_agent_id == current_user.id:
        return serialize_handoff_request(
            db,
            row,
            current_user=current_user,
            conversation=conversation,
            ticket=ticket,
        )
    if row.status != "requested":
        raise HTTPException(
            status_code=409,
            detail="webchat handoff request is not waiting",
        )
    result = assign_handoff_to_agent(
        db,
        request_row=row,
        conversation=conversation,
        user=current_user,
        mode="accepted",
    )
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    if ticket is not None:
        ticket.status = TicketStatus.in_progress
        ticket.conversation_state = ConversationState.human_owned
        ticket.required_action = None
        ticket.updated_at = utc_now()
    db.flush()
    return result


def decline_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    reason_code: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_DECLINE,
        detail="webchat_handoff_decline_requires_capability",
    )
    row = _request_by_id(db, request_id, lock=True)
    conversation, control, ticket = _context(db, row)
    _require_scope(db, current_user=current_user, control=control)
    if row.status != "requested":
        raise HTTPException(
            status_code=409,
            detail="only requested handoffs can be declined",
        )
    voice_session = (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.handoff_request_id == row.id,
            WebchatVoiceSession.status.in_(sorted(VOICE_OPEN_SESSION_STATUSES)),
        )
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )
    if voice_session is not None:
        active_offer = (
            db.query(VoiceRoutingOffer)
            .filter(
                VoiceRoutingOffer.voice_session_id == voice_session.id,
                VoiceRoutingOffer.agent_id == current_user.id,
                VoiceRoutingOffer.status == "offered",
            )
            .first()
        )
        if active_offer is not None:
            decline_voice_offer(
                db,
                voice_session=voice_session,
                user=current_user,
                reason_code=reason_code or "agent_skipped",
                note=note,
            )
            return serialize_handoff_request(
                db,
                row,
                current_user=current_user,
                conversation=conversation,
                ticket=ticket,
            )
    decision = record_routing_decline(
        db,
        request_row=row,
        user_id=current_user.id,
        reason_code=reason_code or "agent_skipped",
        note=note,
    )
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    mark_routing_outcome(
        row,
        outcome="waiting",
        reason_code="agent_declined_current_generation",
    )
    _write_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="handoff.declined",
        actor_id=current_user.id,
        payload={
            "reason_code": decision.reason_code,
            "decline_expires_at": (
                decision.expires_at.isoformat() if decision.expires_at else None
            ),
        },
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.declined",
        target_type="webchat_handoff_request",
        target_id=row.id,
        new_value={
            "reason_code": decision.reason_code,
            "routing_generation": row.routing_generation,
            "expires_at": (
                decision.expires_at.isoformat() if decision.expires_at else None
            ),
        },
    )
    db.flush()
    fill_agent_capacity(db, user=current_user)
    return serialize_handoff_request(
        db,
        row,
        current_user=current_user,
        conversation=conversation,
        ticket=ticket,
    )


def force_takeover_ticket(
    db: Session,
    *,
    ticket_id: int,
    current_user: User,
    reason_code: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
        detail="webchat_handoff_force_takeover_requires_capability",
    )
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
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
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    if control is None:
        raise HTTPException(status_code=409, detail="conversation_control_missing")
    _require_scope(db, current_user=current_user, control=control)
    row = request_handoff(
        db,
        conversation=conversation,
        source="operator_forced",
        trigger_type="force_takeover",
        reason_code=reason_code or "operator_forced_takeover",
        reason_text=note,
        recommended_agent_action="Human agent forced takeover while AI was active.",
        ai_turn_id=conversation.active_ai_turn_id,
        requested_by_actor_type="agent",
        requested_by_user_id=current_user.id,
    )
    if row.status == "accepted" and row.assigned_agent_id != current_user.id:
        previous_agent_id = row.assigned_agent_id
        row.status = "requested"
        row.assigned_agent_id = None
        row.accepted_by_user_id = None
        row.released_at = utc_now()
        start_next_routing_generation(
            row,
            reason_code="supervisor_force_takeover",
        )
        conversation.active_agent_id = None
        conversation.handoff_status = "requested"
        if ticket.assignee_id == previous_agent_id:
            ticket.assignee_id = None
    row.forced_by_user_id = current_user.id
    result = assign_handoff_to_agent(
        db,
        request_row=row,
        conversation=conversation,
        user=current_user,
        mode="forced",
    )
    ticket.status = TicketStatus.in_progress
    ticket.conversation_state = ConversationState.human_owned
    ticket.required_action = None
    conversation.takeover_mode = "forced"
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    db.flush()
    return result


def release_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict[str, Any]:
    capabilities = _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_RELEASE,
        detail="webchat_handoff_release_requires_capability",
    )
    row = _request_by_id(db, request_id, lock=True)
    conversation, control, ticket = _context(db, row)
    _require_scope(db, current_user=current_user, control=control)
    if row.status != "accepted":
        raise HTTPException(
            status_code=409,
            detail="only accepted handoffs can be released",
        )
    if (
        row.assigned_agent_id != current_user.id
        and CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER not in capabilities
    ):
        raise HTTPException(
            status_code=403,
            detail="webchat handoff is owned by another agent",
        )
    previous_agent_id = row.assigned_agent_id
    now = utc_now()
    row.status = "requested"
    row.assigned_agent_id = None
    row.accepted_by_user_id = None
    row.released_at = now
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    row.lock_version += 1
    start_next_routing_generation(row, reason_code="handoff_released")
    conversation.current_handoff_request_id = row.id
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_by = current_user.id
    conversation.ai_suspended_reason = "handoff_released"
    conversation.takeover_mode = None
    conversation.updated_at = now
    if ticket is not None:
        if ticket.assignee_id == previous_agent_id:
            ticket.assignee_id = None
        ticket.status = TicketStatus.pending_assignment
        ticket.conversation_state = ConversationState.human_review_required
        ticket.required_action = (
            row.recommended_agent_action
            or row.reason_code
            or "WebChat handoff waiting for human support"
        )
        ticket.updated_at = now
    task = _operator_task(db, row.id)
    if task is not None:
        task.status = "pending"
        task.assignee_id = None
        task.updated_at = now
    _write_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="handoff.released",
        actor_id=current_user.id,
        payload={"previous_agent_id": previous_agent_id},
    )
    db.flush()
    request_handoff(
        db,
        conversation=conversation,
        source=row.source,
        trigger_type=row.trigger_type,
        reason_code=row.reason_code,
        reason_text=row.reason_text,
        recommended_agent_action=row.recommended_agent_action,
        requested_by_actor_type="agent",
        requested_by_user_id=current_user.id,
    )
    if previous_agent_id is not None:
        previous = db.get(User, previous_agent_id)
        if previous is not None:
            fill_agent_capacity(db, user=previous)
    return serialize_handoff_request(
        db,
        row,
        current_user=current_user,
        conversation=conversation,
        ticket=ticket,
    )


def resume_ai_for_handoff(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict[str, Any]:
    capabilities = resolve_capabilities(current_user, db)
    row = _request_by_id(db, request_id, lock=True)
    conversation, control, ticket = _context(db, row)
    _require_scope(db, current_user=current_user, control=control)
    if not can_resume_handoff(
        handoff=row,
        conversation=conversation,
        user_id=current_user.id,
        capabilities=capabilities,
    ):
        raise HTTPException(
            status_code=403,
            detail="handoff_resume_not_authorized",
        )
    previous_agent_id = row.assigned_agent_id
    now = utc_now()
    row.status = "resumed_ai"
    row.assigned_agent_id = None
    row.closed_at = now
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    mark_routing_outcome(
        row,
        outcome="fallback_selected",
        reason_code="ai_resumed",
        fallback_action="resume_ai",
    )
    row.lock_version += 1
    row.updated_at = now
    conversation.current_handoff_request_id = None
    conversation.handoff_status = "none"
    conversation.active_agent_id = None
    conversation.ai_suspended = False
    conversation.ai_suspended_at = None
    conversation.ai_suspended_by = None
    conversation.ai_suspended_reason = None
    conversation.takeover_mode = None
    conversation.updated_at = now
    if ticket is not None:
        ticket.required_action = None
        ticket.conversation_state = ConversationState.ai_active
        if ticket.assignee_id == previous_agent_id:
            ticket.assignee_id = None
        ticket.updated_at = now
    task = _operator_task(db, row.id)
    if task is not None:
        task.status = "resolved"
        task.resolved_at = now
        task.updated_at = now
    _write_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="ai.resumed",
        actor_id=current_user.id,
        payload={"previous_agent_id": previous_agent_id},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.resume_ai",
        target_type="webchat_handoff_request",
        target_id=row.id,
        new_value={
            "status": row.status,
            "previous_agent_id": previous_agent_id,
        },
    )
    db.flush()
    if previous_agent_id is not None:
        previous = db.get(User, previous_agent_id)
        if previous is not None:
            fill_agent_capacity(db, user=previous)
    return serialize_handoff_request(
        db,
        row,
        current_user=current_user,
        conversation=conversation,
        ticket=ticket,
    )


def ensure_can_reply_in_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    current_user: User,
) -> None:
    request_id = conversation.current_handoff_request_id
    if not request_id and conversation.handoff_status in {None, "none"}:
        if (
            conversation.active_ai_status in AI_ACTIVE_STATUSES
            and not conversation.ai_suspended
        ):
            raise HTTPException(
                status_code=409,
                detail="webchat ai is active; force takeover before replying",
            )
        return
    row = db.get(WebchatHandoffRequest, request_id) if request_id else None
    if (
        row is not None
        and row.status == "accepted"
        and row.assigned_agent_id == current_user.id
        and conversation.active_agent_id == current_user.id
    ):
        return
    if row is not None and row.status == "accepted":
        raise HTTPException(
            status_code=409,
            detail="webchat handoff is owned by another agent",
        )
    raise HTTPException(
        status_code=409,
        detail="webchat handoff must be accepted before replying",
    )


__all__ = [
    "AI_ACTIVE_STATUSES",
    "OPEN_HANDOFF_STATUSES",
    "TERMINAL_HANDOFF_STATUSES",
    "accept_handoff_request",
    "decline_handoff_request",
    "ensure_can_reply_in_handoff",
    "force_takeover_ticket",
    "list_handoff_queue",
    "release_handoff_request",
    "request_webchat_handoff",
    "resume_ai_for_handoff",
    "serialize_handoff_request",
]
