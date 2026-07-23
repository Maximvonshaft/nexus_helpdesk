from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..models_agent_routing import ConversationControl
from ..models_agent_runtime import AgentToolConfirmation
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatAITurn,
    WebchatConversation,
    WebchatEvent,
    WebchatMessage,
)
from .agent_confirmation_service import (
    open_confirmation_arguments,
    record_confirmation_execution_result,
    resolve_confirmation_from_customer_message,
)
from .agent_runtime.access_policy import resolve_webchat_agent_access
from .agent_runtime.tool_adapter import (
    AgentExecutionContext,
    ToolObservation,
    execute_agent_tool_calls,
)
from .background_job_transaction_boundary import (
    commit_webchat_agent_provider_boundary,
)
from .webchat_ai_decision_runtime.schemas import AIDecisionToolCall
from .webchat_ai_service import (
    AI_AUTHOR_LABEL,
    process_webchat_ai_reply_job as _run_agent_reply,
)
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
        ticket = db.get(Ticket, ticket_id) if ticket_id is not None else None
        if ticket is not None or visitor_message.ticket_id is not None:
            raise RuntimeError("ticketless webchat job payload mismatch")
        return conversation, None, visitor_message

    if ticket_id is not None and ticket_id != conversation.ticket_id:
        raise RuntimeError("ticket-backed webchat job payload mismatch")
    ticket = db.get(Ticket, conversation.ticket_id)
    if ticket is None:
        raise RuntimeError("conversation ticket not found")
    if visitor_message.ticket_id is None:
        # A confirmed Tool can create the Ticket in an independent transaction.
        # Recover the current message from the Conversation authority after a
        # Worker restart without duplicating the Tool side effect.
        visitor_message.ticket_id = ticket.id
        db.flush()
    elif visitor_message.ticket_id != ticket.id:
        raise RuntimeError("ticket-backed webchat job payload mismatch")
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


def _complete_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    turn: WebchatAITurn | None,
    result: dict[str, Any],
) -> None:
    if turn is None or result.get("turn_finalized"):
        return
    complete_ai_turn_with_reply(
        db,
        conversation=conversation,
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


def _resolve_customer_confirmation(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
) -> dict[str, Any] | None:
    resolution = resolve_confirmation_from_customer_message(
        db,
        conversation=conversation,
        message=visitor_message,
    )
    if resolution is None or resolution.get("decision") in {None, "ambiguous"}:
        return resolution
    db.add(
        WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            event_type="agent.tool_confirmation.resolved",
            payload_json=json.dumps(
                {
                    "confirmation_id": resolution.get("confirmation_id"),
                    "tool_name": resolution.get("tool_name"),
                    "decision": resolution.get("decision"),
                    "status": resolution.get("status"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    )
    db.flush()
    # Agent Runtime and Tool execution deliberately use independent Sessions.
    # Customer authorization must be durable before either side-effect boundary.
    commit_webchat_agent_provider_boundary(db)
    return resolution


def _conversation_control(
    db: Session,
    *,
    conversation_id: int,
) -> ConversationControl | None:
    return (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation_id)
        .first()
    )


def _blocked_observation(tool_name: str, error_code: str) -> ToolObservation:
    return ToolObservation(
        tool_name=tool_name,
        ok=False,
        status="blocked",
        result={},
        error_code=error_code,
    )


def _execute_confirmed_action(
    db: Session,
    *,
    resolution: dict[str, Any] | None,
    conversation: WebchatConversation,
    ticket: Ticket | None,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
) -> tuple[
    WebchatConversation,
    Ticket | None,
    WebchatMessage,
    WebchatAITurn | None,
    ToolObservation | None,
]:
    if resolution is None or resolution.get("status") != "confirmed":
        return conversation, ticket, visitor_message, turn, None
    confirmation_id = str(resolution.get("confirmation_id") or "").strip()
    confirmation = (
        db.query(AgentToolConfirmation)
        .filter(
            AgentToolConfirmation.public_id == confirmation_id,
            AgentToolConfirmation.conversation_id == conversation.id,
            AgentToolConfirmation.status == "confirmed",
        )
        .first()
    )
    if confirmation is None:
        return conversation, ticket, visitor_message, turn, None

    tool_name = confirmation.tool_name
    arguments = open_confirmation_arguments(confirmation)
    access = resolve_webchat_agent_access()
    control = _conversation_control(db, conversation_id=conversation.id)
    observation: ToolObservation
    if tool_name not in access.allowed_tools:
        observation = _blocked_observation(tool_name, "confirmed_tool_not_available")
    else:
        context = AgentExecutionContext(
            tenant_key=conversation.tenant_key,
            channel_key=conversation.channel_key,
            session_id=(
                conversation.runtime_session_id
                or f"webchat:{conversation.tenant_key}:{conversation.channel_key}:{conversation.public_id}"
            ),
            request_id=f"confirmed-action:{confirmation.public_id}",
            customer_message=visitor_message.body_text or visitor_message.body or "",
            market_id=getattr(ticket, "market_id", None),
            conversation_id=conversation.id,
            ticket_id=ticket.id if ticket is not None else None,
            customer_id=(
                ticket.customer_id
                if ticket is not None
                else control.customer_id
                if control is not None
                else None
            ),
            country_code=(
                getattr(ticket, "country_code", None)
                if ticket is not None
                else control.country_code
                if control is not None
                else None
            ),
            ai_turn_id=turn.id if turn is not None else None,
            allowed_tools=frozenset(access.allowed_tools),
            granted_permissions=frozenset(access.granted_permissions),
            actor_capabilities=frozenset(access.actor_capabilities),
        )
        call = AIDecisionToolCall(
            tool_name=tool_name,
            arguments=arguments,
        )
        # Release read locks before the canonical Tool Adapter opens its own
        # transaction on the same Engine.
        commit_webchat_agent_provider_boundary(db)
        observations = execute_agent_tool_calls(
            db,
            calls=[call],
            context=context,
        )
        observation = (
            observations[0]
            if observations
            else _blocked_observation(tool_name, "tool_execution_result_missing")
        )

    db.expire_all()
    record_confirmation_execution_result(
        db,
        confirmation_id=confirmation_id,
        execution={
            "ok": observation.ok,
            "status": observation.status,
            "error_code": observation.error_code,
            "result": observation.result,
        },
    )
    conversation = db.get(WebchatConversation, conversation.id)
    visitor_message = db.get(WebchatMessage, visitor_message.id)
    turn = db.get(WebchatAITurn, turn.id) if turn is not None else None
    if conversation is None or visitor_message is None:
        raise RuntimeError("confirmed Agent Tool context disappeared")
    ticket = db.get(Ticket, conversation.ticket_id) if conversation.ticket_id else None
    if ticket is not None:
        visitor_message.ticket_id = ticket.id
        if turn is not None:
            turn.ticket_id = ticket.id
            turn.updated_at = utc_now()
    db.add(
        WebchatEvent(
            conversation_id=conversation.id,
            ticket_id=ticket.id if ticket is not None else None,
            event_type="agent.tool_confirmation.executed",
            payload_json=json.dumps(
                {
                    "confirmation_id": confirmation_id,
                    "tool_name": tool_name,
                    "ok": observation.ok,
                    "status": observation.status,
                    "error_code": observation.error_code,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    )
    db.flush()
    commit_webchat_agent_provider_boundary(db)
    return conversation, ticket, visitor_message, turn, observation


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
    resolution = _resolve_customer_confirmation(
        db,
        conversation=conversation,
        visitor_message=visitor_message,
    )
    conversation, ticket, visitor_message, turn, _observation = _execute_confirmed_action(
        db,
        resolution=resolution,
        conversation=conversation,
        ticket=ticket,
        visitor_message=visitor_message,
        turn=turn,
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
    else:
        commit_webchat_agent_provider_boundary(db)
        result = _run_agent_reply(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id if ticket else None,
            visitor_message_id=visitor_message.id,
            ai_turn_id=turn.id if turn else None,
        )

    _complete_turn(
        db,
        conversation=conversation,
        turn=turn,
        result=result or {},
    )
    return result
