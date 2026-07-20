from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage
from .conversation_ai_service import process_ticketless_ai_reply
from .webchat_ai_service import AI_AUTHOR_LABEL, process_webchat_ai_reply_job as _run_ticket_reply
from .webchat_ai_turn_service import (
    AI_TURN_OPEN_STATUSES,
    cancel_open_ai_turns_for_handoff,
    complete_ai_turn_with_reply,
    is_ai_suspended_for_handoff,
    latest_visitor_message_id,
    mark_ai_turn_bridge_calling,
    mark_ai_turn_processing,
    suppress_stale_reply_if_needed,
)
from .webchat_osr_audit_service import audit_completed_webchat_ai_turn


settings = get_settings()
LOGGER = logging.getLogger("nexusdesk")


def _load_context(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int | None,
    visitor_message_id: int,
) -> tuple[WebchatConversation, Ticket | None, WebchatMessage]:
    conversation = db.get(WebchatConversation, conversation_id)
    normalized_ticket_id = int(ticket_id or 0)
    ticket = db.get(Ticket, normalized_ticket_id) if normalized_ticket_id > 0 else None
    visitor_message = db.get(WebchatMessage, visitor_message_id)
    if conversation is None:
        raise RuntimeError(
            f"webchat conversation not found: conversation_id={conversation_id}"
        )
    if visitor_message is None:
        raise RuntimeError(
            f"visitor message not found: visitor_message_id={visitor_message_id}"
        )
    if visitor_message.conversation_id != conversation.id:
        raise RuntimeError("webchat job payload mismatch")
    if conversation.ticket_id is None:
        if ticket is not None or visitor_message.ticket_id is not None:
            raise RuntimeError("ticketless webchat job payload mismatch")
    else:
        if ticket is None or ticket.id != conversation.ticket_id:
            raise RuntimeError("ticket-backed webchat job payload mismatch")
        if visitor_message.ticket_id != ticket.id:
            raise RuntimeError("webchat message ticket mismatch")
    return conversation, ticket, visitor_message


def _open_turn_for_message(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
) -> WebchatAITurn | None:
    candidates = (
        db.query(WebchatAITurn)
        .filter(
            WebchatAITurn.conversation_id == conversation.id,
            WebchatAITurn.status.in_(AI_TURN_OPEN_STATUSES),
        )
        .order_by(WebchatAITurn.id.asc())
        .all()
    )
    for turn in candidates:
        if (
            turn.trigger_message_id == visitor_message.id
            or turn.latest_visitor_message_id == visitor_message.id
            or conversation.active_ai_turn_id == turn.id
        ):
            return turn
    return None


def _agent_reply_exists(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
) -> bool:
    return bool(
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
            WebchatMessage.id > visitor_message.id,
            WebchatMessage.author_label == AI_AUTHOR_LABEL,
        )
        .first()
    )


def _audit_runtime_turn_non_blocking(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if turn is None or ticket is None:
        return None
    try:
        with db.begin_nested():
            return audit_completed_webchat_ai_turn(
                db,
                conversation=conversation,
                ticket=ticket,
                visitor_message=visitor_message,
                turn=turn,
                result=result,
            )
    except Exception as exc:  # pragma: no cover - audit must never block replies
        LOGGER.warning(
            "webchat_runtime_audit_failed_non_blocking",
            extra={
                "event_payload": {
                    "conversation_id": conversation.id,
                    "ticket_id": ticket.id,
                    "visitor_message_id": visitor_message.id,
                    "ai_turn_id": turn.id,
                    "error_type": type(exc).__name__,
                }
            },
        )
        return None


def _complete_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
    result: dict[str, Any],
) -> None:
    if turn is None:
        return
    complete_ai_turn_with_reply(
        db,
        conversation=conversation,
        turn=turn,
        result=result,
    )
    _audit_runtime_turn_non_blocking(
        db,
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        turn=turn,
        result=result,
    )


def _record_disabled(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
) -> None:
    payload = {
        "conversation_id": conversation.id,
        "visitor_message_id": visitor_message.id,
        "ai_turn_id": turn.id if turn else None,
    }
    if ticket is not None:
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.internal_note_added,
                note="Webchat AI auto reply disabled",
                payload_json=json.dumps(payload),
            )
        )
    else:
        db.add(
            WebchatEvent(
                conversation_id=conversation.id,
                ticket_id=None,
                event_type="ai_turn.disabled",
                payload_json=json.dumps(payload),
            )
        )
    db.flush()


def process_webchat_ai_reply_job(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int | None,
    visitor_message_id: int,
) -> dict[str, Any]:
    conversation, ticket, visitor_message = _load_context(
        db,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        visitor_message_id=visitor_message_id,
    )
    turn = _open_turn_for_message(
        db,
        conversation=conversation,
        visitor_message=visitor_message,
    )

    if is_ai_suspended_for_handoff(conversation):
        cancel_open_ai_turns_for_handoff(
            db,
            conversation=conversation,
            actor_id=None,
            reason_code="handoff_ai_suspended_before_runtime",
        )
        return {
            "status": "skipped",
            "reason": "handoff_ai_suspended",
            "reply_source": "suppressed",
        }

    if turn is not None and turn.status == "queued":
        mark_ai_turn_processing(db, conversation=conversation, turn=turn)
        cutoff_id = latest_visitor_message_id(
            db,
            conversation_id=conversation.id,
        )
        mark_ai_turn_bridge_calling(
            db,
            conversation=conversation,
            turn=turn,
            context_cutoff_message_id=cutoff_id,
        )

    if _agent_reply_exists(
        db,
        conversation=conversation,
        visitor_message=visitor_message,
    ):
        result = {
            "status": "skipped",
            "reason": "agent_reply_already_exists",
            "reply_source": "existing_reply",
        }
        _complete_turn(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            turn=turn,
            result=result,
        )
        return result

    if suppress_stale_reply_if_needed(
        db,
        conversation=conversation,
        turn=turn,
        reason="newer_message_before_reply",
    ):
        return {
            "status": "superseded",
            "reason": "newer_message_before_reply",
            "reply_source": "suppressed",
        }

    if (settings.webchat_ai_auto_reply_mode or "runtime").lower() == "off":
        _record_disabled(
            db,
            conversation=conversation,
            ticket=ticket,
            visitor_message=visitor_message,
            turn=turn,
        )
        result = {
            "status": "skipped",
            "reason": "webchat_ai_auto_reply_off",
            "reply_source": "off",
        }
    elif ticket is not None:
        result = _run_ticket_reply(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            visitor_message_id=visitor_message.id,
            ai_turn_id=turn.id if turn else None,
        )
    else:
        result = process_ticketless_ai_reply(
            db,
            conversation=conversation,
            visitor_message=visitor_message,
            turn=turn,
        )

    _complete_turn(
        db,
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        turn=turn,
        result=result or {},
    )
    return result
