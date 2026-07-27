from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _load_job_payload(job: Any) -> dict[str, Any]:
    try:
        value = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _lock_one(query: Any, db: Session):
    if (
        getattr(db, "bind", None) is not None
        and db.bind.dialect.name.startswith("postgresql")
    ):
        query = query.with_for_update()
    return query.first()


def finalize_dead_webchat_ai_job(db: Session, job: Any) -> None:
    """Commit one customer terminal outcome when the canonical AI job is dead."""

    from ..enums import (
        ConversationState,
        EventType,
        MessageStatus,
        SourceChannel,
        TicketStatus,
    )
    from ..models import Ticket
    from ..utils.time import utc_now
    from ..webchat_models import (
        WebchatAITurn,
        WebchatConversation,
        WebchatMessage,
    )
    from . import background_jobs
    from .agent_runtime.terminal_reply import customer_visible_fallback
    from .ai_reply_contract import build_ai_reply_contract
    from .customer_language import resolve_conversation_language
    from .customer_visible_message_service import create_customer_visible_message
    from .customer_visible_policy import evaluate_customer_visible_policy
    from .sla_service import evaluate_sla, update_first_response
    from .webchat_ai_turn_service import (
        complete_ai_turn_with_reply,
        is_ai_suspended_for_handoff,
        latest_visitor_message_id,
        supersede_ai_turn,
    )

    if job.job_type != background_jobs.WEBCHAT_AI_REPLY_JOB:
        return
    if _status_value(job.status) != background_jobs.JobStatus.dead.value:
        return

    payload = _load_job_payload(job)
    raw_turn_id = payload.get("ai_turn_id")
    turn = None
    if raw_turn_id is not None:
        try:
            turn = _lock_one(
                db.query(WebchatAITurn).filter(WebchatAITurn.id == int(raw_turn_id)),
                db,
            )
        except (TypeError, ValueError):
            turn = None
    if turn is None:
        turn = _lock_one(
            db.query(WebchatAITurn)
            .filter(WebchatAITurn.job_id == job.id)
            .order_by(WebchatAITurn.id.desc()),
            db,
        )
    if turn is None:
        raise RuntimeError("dead_webchat_ai_job_turn_missing")

    conversation = _lock_one(
        db.query(WebchatConversation).filter(
            WebchatConversation.id == turn.conversation_id
        ),
        db,
    )
    visitor_message = db.get(
        WebchatMessage,
        turn.latest_visitor_message_id or turn.trigger_message_id,
    )
    if conversation is None or visitor_message is None:
        raise RuntimeError("dead_webchat_ai_job_context_missing")
    if visitor_message.conversation_id != conversation.id:
        raise RuntimeError("dead_webchat_ai_job_context_mismatch")

    existing = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.ai_turn_id == turn.id,
            WebchatMessage.direction == "agent",
        )
        .order_by(WebchatMessage.id.asc())
        .first()
    )
    if existing is not None:
        complete_ai_turn_with_reply(
            db,
            conversation=conversation,
            turn=turn,
            result={
                "status": "done",
                "message_id": existing.id,
                "reply_source": "agent_runtime:fallback",
                "fallback_reason": "background_job_exhausted",
                "runtime_trace": {
                    "error_code": "background_job_exhausted",
                    "attempt_count": int(job.attempt_count or 0),
                },
            },
        )
        job.last_error = "webchat_ai_attempts_exhausted"
        return

    if not turn.is_public_reply_allowed or is_ai_suspended_for_handoff(conversation):
        if turn.status not in {"completed", "superseded", "cancelled"}:
            supersede_ai_turn(
                db,
                conversation=conversation,
                turn=turn,
                reason="handoff_started_before_terminal_fallback",
            )
        job.last_error = "webchat_ai_terminal_fallback_suppressed_by_handoff"
        return

    latest_id = latest_visitor_message_id(db, conversation_id=conversation.id)
    cutoff_id = (
        turn.context_cutoff_message_id
        or turn.latest_visitor_message_id
        or turn.trigger_message_id
    )
    if latest_id is not None and cutoff_id is not None and latest_id > cutoff_id:
        if turn.status not in {"completed", "superseded", "cancelled"}:
            supersede_ai_turn(
                db,
                conversation=conversation,
                turn=turn,
                reason="newer_message_before_terminal_fallback",
            )
        job.last_error = "webchat_ai_terminal_fallback_suppressed_as_stale"
        return

    later_agent_message = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
            WebchatMessage.id > visitor_message.id,
        )
        .order_by(WebchatMessage.id.asc())
        .first()
    )
    if later_agent_message is not None:
        if turn.status not in {"completed", "superseded", "cancelled"}:
            supersede_ai_turn(
                db,
                conversation=conversation,
                turn=turn,
                reason="customer_visible_reply_already_committed",
            )
        job.last_error = "webchat_ai_terminal_fallback_suppressed_existing_reply"
        return

    previous_messages = [
        row[0]
        for row in (
            db.query(WebchatMessage.body)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.direction == "visitor",
                WebchatMessage.id < visitor_message.id,
            )
            .order_by(WebchatMessage.id.asc())
            .all()
        )
    ]
    language = resolve_conversation_language(
        visitor_message.body,
        previous_customer_messages=previous_messages,
    ).language
    body = customer_visible_fallback(language, visitor_message.body)
    policy = evaluate_customer_visible_policy(body)
    if not policy.allowed or not policy.normalized_body.strip():
        raise RuntimeError("customer_visible_terminal_fallback_rejected")
    body = policy.normalized_body

    is_whatsapp = (
        str(conversation.channel_key or "").strip().lower()
        == SourceChannel.whatsapp.value
    )
    channel = SourceChannel.whatsapp if is_whatsapp else SourceChannel.web_chat
    ticket = db.get(Ticket, turn.ticket_id) if turn.ticket_id is not None else None
    if conversation.ticket_id is not None and (
        ticket is None or ticket.id != conversation.ticket_id
    ):
        raise RuntimeError("dead_webchat_ai_job_ticket_mismatch")

    safe_trace = {
        "error_code": "background_job_exhausted",
        "attempt_count": int(job.attempt_count or 0),
    }
    contract = build_ai_reply_contract(
        body=body,
        runtime_trace=safe_trace,
        safety_status="passed",
        reply_type="clarifying_question",
        channel=channel.value,
    )
    provider_status = (
        "whatsapp_ai_terminal_fallback_queued"
        if is_whatsapp
        else "webchat_ai_terminal_fallback_sent"
    )
    visible = create_customer_visible_message(
        db,
        ticket=ticket,
        conversation=conversation,
        channel=channel,
        body=body,
        origin="provider_runtime",
        created_by=None,
        provider_status=provider_status,
        ai_contract=contract,
        outbound_status=None if is_whatsapp else MessageStatus.sent,
        ai_turn_id=turn.id,
        delivery_status="queued" if is_whatsapp else "sent",
        metadata_json={
            "terminal_fallback": True,
            "reason_code": "background_job_exhausted",
            "attempt_count": int(job.attempt_count or 0),
            "language": language,
        },
        author_label="AI Assistant",
        safety_level=policy.level,
        safety_reasons_json=json.dumps(policy.reasons, ensure_ascii=False),
        create_external_comment=ticket is not None,
        event_type=(
            EventType.outbound_queued
            if is_whatsapp
            else EventType.outbound_sent
            if ticket is not None
            else None
        ),
        event_note=(
            "WhatsApp Agent terminal fallback queued"
            if is_whatsapp
            else "Webchat Agent terminal fallback sent"
        ),
        event_payload={
            "conversation_id": conversation.id,
            "ticket_id": ticket.id if ticket else None,
            "visitor_message_id": visitor_message.id,
            "ai_turn_id": turn.id,
            "reply_source": "agent_runtime:fallback",
            "provider_status": provider_status,
            "reason_code": "background_job_exhausted",
        },
    )
    if visible.webchat_message is None:
        raise RuntimeError("customer_visible_terminal_fallback_not_created")

    now = utc_now()
    if ticket is not None:
        ticket.status = TicketStatus.waiting_customer
        ticket.conversation_state = ConversationState.waiting_customer
        ticket.last_ai_update = body
        ticket.last_runtime_reply_at = now
        ticket.updated_at = now
        update_first_response(ticket)
        evaluate_sla(ticket, db)
    conversation.updated_at = now
    conversation.last_seen_at = now
    complete_ai_turn_with_reply(
        db,
        conversation=conversation,
        turn=turn,
        result={
            "status": "done",
            "message_id": visible.webchat_message.id,
            "reply_source": "agent_runtime:fallback",
            "fallback_reason": "background_job_exhausted",
            "runtime_trace": safe_trace,
        },
    )
    job.last_error = "webchat_ai_attempts_exhausted"


__all__ = ["finalize_dead_webchat_ai_job"]
