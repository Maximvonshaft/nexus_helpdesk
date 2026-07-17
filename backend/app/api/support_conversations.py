from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import ConversationState, SourceChannel, TicketStatus
from ..models import Ticket
from ..services.permissions import (
    CAP_TICKET_READ,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    ensure_capability,
    resolve_capabilities,
)
from ..services.support_conversation_privacy import (
    mask_support_contact,
    mask_support_display_name,
    safe_support_message_preview,
    safe_support_tracking_reference,
)
from ..services.support_conversation_scope import apply_support_ticket_scope
from ..services.webchat_ai_turn_service import AI_TURN_TYPING_STATUSES, ai_snapshot
from ..services.webchat_service import admin_reply
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatAITurn,
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .deps import get_current_user

router = APIRouter(
    prefix="/api/support/conversations",
    tags=["support-conversations"],
)


class SupportConversationReplyRequest(BaseModel):
    session_key: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=40000)
    evidence_reference_id: int | None = Field(default=None, ge=1)


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _channel(conversation: WebchatConversation, ticket: Ticket) -> str:
    source_channel = (
        _enum_value(ticket.source_channel).lower()
        if ticket.source_channel
        else ""
    )
    channel_key = (conversation.channel_key or "").lower()
    origin = (conversation.origin or "").lower()
    if (
        source_channel == SourceChannel.whatsapp.value
        or channel_key == "whatsapp"
        or "whatsapp" in origin
    ):
        return "whatsapp"
    if (
        source_channel == SourceChannel.web_chat.value
        or channel_key in {"webchat", "website", "default"}
    ):
        return "webchat"
    return source_channel or channel_key or "support"


def _whatsapp_predicate():
    return or_(
        Ticket.source_channel == SourceChannel.whatsapp,
        func.lower(
            func.coalesce(WebchatConversation.channel_key, "")
        )
        == "whatsapp",
        func.lower(
            func.coalesce(WebchatConversation.origin, "")
        ).like("%whatsapp%"),
    )


def _channel_predicate(channel: str):
    if channel == "whatsapp":
        return _whatsapp_predicate()
    if channel == "webchat":
        return and_(
            ~_whatsapp_predicate(),
            or_(
                Ticket.source_channel == SourceChannel.web_chat,
                func.lower(
                    func.coalesce(
                        WebchatConversation.channel_key,
                        "",
                    )
                ).in_(("webchat", "website", "default")),
            ),
        )
    raise ValueError("unsupported_support_channel")


def _session_key(conversation: WebchatConversation, ticket: Ticket) -> str:
    return f"{_channel(conversation, ticket)}:{conversation.public_id}"


def _author(direction: str | None) -> str:
    normalized = (direction or "").lower()
    if normalized in {"visitor", "customer", "user"}:
        return "customer"
    if normalized in {"agent", "human"}:
        return "agent"
    return "ai" if normalized == "ai" else "system"


def _load_conversation(
    db: Session,
    session_key: str,
    *,
    current_user,
) -> tuple[WebchatConversation, Ticket]:
    key = (session_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_key_required",
        )
    requested_channel, separator, public_id = key.partition(":")
    if not separator:
        public_id = requested_channel
        requested_channel = ""

    query = (
        db.query(WebchatConversation, Ticket)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .filter(WebchatConversation.public_id == public_id)
    )
    row = apply_support_ticket_scope(query, current_user, db).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="support_conversation_not_found",
        )
    conversation, ticket = row
    if (
        requested_channel
        and requested_channel.lower() != _channel(conversation, ticket)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="support_conversation_not_found",
        )
    return conversation, ticket


def _latest_message_subquery(db: Session):
    return (
        db.query(
            WebchatMessage.conversation_id.label("conversation_id"),
            func.max(WebchatMessage.id).label("last_message_id"),
        )
        .group_by(WebchatMessage.conversation_id)
        .subquery()
    )


def _ai_pending(conversation: WebchatConversation) -> bool:
    return bool(
        getattr(conversation, "active_ai_turn_id", None)
        and getattr(conversation, "active_ai_status", None)
        in AI_TURN_TYPING_STATUSES
        and not getattr(conversation, "ai_suspended", False)
    )


def _ai_blocks_manual_reply(conversation: WebchatConversation) -> bool:
    return bool(
        getattr(conversation, "active_ai_status", None)
        in AI_TURN_TYPING_STATUSES
        and not getattr(conversation, "ai_suspended", False)
    )


def _parse_runtime_trace(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((percentile / 100) * (len(ordered) - 1))
    return int(ordered[max(0, min(len(ordered) - 1, index))])


def _latency_stats(values: list[int]) -> dict[str, int | None]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 50),
        "p90_ms": _percentile(values, 90),
        "max_ms": max(values) if values else None,
    }


def _runtime_latency_summary(
    db: Session,
    *,
    since,
    current_user,
) -> dict[str, Any]:
    query = (
        db.query(WebchatAITurn)
        .join(Ticket, Ticket.id == WebchatAITurn.ticket_id)
        .filter(WebchatAITurn.created_at >= since)
    )
    turns = (
        apply_support_ticket_scope(query, current_user, db)
        .order_by(WebchatAITurn.id.desc())
        .limit(120)
        .all()
    )
    total_turn_ms: list[int] = []
    bridge_elapsed_ms: list[int] = []
    runtime_total_ms: list[int] = []
    runtime_load_ms: list[int] = []
    runtime_prompt_eval_ms: list[int] = []
    runtime_eval_ms: list[int] = []
    by_latency_class: Counter[str] = Counter()
    cold_load_count = 0
    slow_prompt_eval_count = 0
    failed_count = 0

    for turn in turns:
        if turn.status in {"failed", "timeout"}:
            failed_count += 1
        if turn.created_at and (turn.completed_at or turn.updated_at):
            total_turn_ms.append(
                max(
                    0,
                    int(
                        (
                            (turn.completed_at or turn.updated_at)
                            - turn.created_at
                        ).total_seconds()
                        * 1000
                    ),
                )
            )
        if isinstance(turn.bridge_elapsed_ms, int):
            bridge_elapsed_ms.append(turn.bridge_elapsed_ms)

        trace = _parse_runtime_trace(turn.runtime_trace_json)
        by_latency_class[
            str(trace.get("latency_class") or "unknown")
        ] += 1
        usage = (
            trace.get("runtime_usage")
            if isinstance(trace.get("runtime_usage"), dict)
            else {}
        )
        for key, bucket in (
            ("total_duration_ms", runtime_total_ms),
            ("load_duration_ms", runtime_load_ms),
            ("prompt_eval_duration_ms", runtime_prompt_eval_ms),
            ("eval_duration_ms", runtime_eval_ms),
        ):
            value = usage.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                bucket.append(int(value))

        load_value = usage.get("load_duration_ms")
        prompt_value = usage.get("prompt_eval_duration_ms")
        if (
            isinstance(load_value, (int, float))
            and not isinstance(load_value, bool)
            and load_value >= 1000
        ):
            cold_load_count += 1
        if (
            isinstance(prompt_value, (int, float))
            and not isinstance(prompt_value, bool)
            and prompt_value >= 1500
        ):
            slow_prompt_eval_count += 1

    return {
        "sample_count": len(turns),
        "failed_count": failed_count,
        "cold_load_count": cold_load_count,
        "slow_prompt_eval_count": slow_prompt_eval_count,
        "by_latency_class": dict(by_latency_class),
        "total_turn": _latency_stats(total_turn_ms),
        "bridge": _latency_stats(bridge_elapsed_ms),
        "runtime_total": _latency_stats(runtime_total_ms),
        "runtime_load": _latency_stats(runtime_load_ms),
        "runtime_prompt_eval": _latency_stats(runtime_prompt_eval_ms),
        "runtime_eval": _latency_stats(runtime_eval_ms),
    }


def _conversation_out(
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    last_message: WebchatMessage | None,
    current_user,
    capabilities: set[str],
) -> dict[str, Any]:
    channel = _channel(conversation, ticket)
    handoff_status = getattr(conversation, "handoff_status", None) or "none"
    active_agent_id = getattr(conversation, "active_agent_id", None)
    raw_display_name = (
        conversation.visitor_name
        or conversation.visitor_ref
        or ticket.ticket_no
    )
    raw_contact = (
        conversation.visitor_phone
        or conversation.visitor_email
    )
    raw_latest_message = (
        (last_message.body_text or last_message.body)
        if last_message
        else ticket.last_customer_message
    )
    item: dict[str, Any] = {
        "session_key": _session_key(conversation, ticket),
        "conversation_id": conversation.public_id,
        "channel": channel,
        "source": conversation.origin or channel,
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "status": _enum_value(ticket.status),
        "conversation_state": _enum_value(ticket.conversation_state),
        "display_name": mask_support_display_name(
            raw_display_name,
            fallback=ticket.ticket_no,
        ),
        "customer_contact": mask_support_contact(raw_contact),
        "updated_at": _iso(conversation.updated_at or ticket.updated_at),
        "last_seen_at": _iso(conversation.last_seen_at),
        "latest_message": safe_support_message_preview(
            raw_latest_message,
            limit=160,
        ),
        "latest_author": (
            _author(last_message.direction)
            if last_message
            else None
        ),
        "needs_human": bool(
            ticket.conversation_state
            == ConversationState.human_review_required
            or ticket.required_action
            or handoff_status in {"requested", "accepted"}
        ),
        "required_action": ticket.required_action,
        "handoff_status": handoff_status,
        "handoff_request_id": getattr(
            conversation,
            "current_handoff_request_id",
            None,
        ),
        "active_agent_id": active_agent_id,
        "ai_status": getattr(conversation, "active_ai_status", None),
        "ai_suspended": bool(
            getattr(conversation, "ai_suspended", False)
        ),
        "tracking_number_present": bool(ticket.tracking_number),
        "tracking_reference": safe_support_tracking_reference(
            ticket.tracking_number
        ),
        "pii_minimized": True,
        "can_force_takeover": bool(
            ticket.id
            and CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities
        ),
        "can_accept": bool(
            handoff_status == "requested"
            and CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities
        ),
        "can_release": bool(
            handoff_status == "accepted"
            and active_agent_id == current_user.id
            and CAP_WEBCHAT_HANDOFF_RELEASE in capabilities
        ),
        "can_resume_ai": bool(
            handoff_status in {"requested", "accepted"}
            and CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities
        ),
        "can_reply": bool(
            ticket.id
            and not _ai_blocks_manual_reply(conversation)
            and (
                handoff_status == "none"
                or (
                    handoff_status == "accepted"
                    and active_agent_id == current_user.id
                )
            )
        ),
    }
    item.update(ai_snapshot(conversation))
    return item


@router.get("")
def list_support_conversations(
    q: str | None = Query(default=None, max_length=120),
    channel: str | None = Query(
        default=None,
        pattern="^(all|webchat|whatsapp)$",
    ),
    view: str = Query(
        default="open",
        pattern="^(open|needs_human|ai_active|mine|all|closed)$",
    ),
    limit: int = Query(default=80, ge=1, le=120),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_capability(
        current_user,
        CAP_TICKET_READ,
        db,
        message="support_conversation_read_requires_capability",
    )
    capabilities = resolve_capabilities(current_user, db)
    latest_message_ids = _latest_message_subquery(db)
    query = (
        db.query(WebchatConversation, Ticket, WebchatMessage)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .outerjoin(
            latest_message_ids,
            latest_message_ids.c.conversation_id
            == WebchatConversation.id,
        )
        .outerjoin(
            WebchatMessage,
            WebchatMessage.id
            == latest_message_ids.c.last_message_id,
        )
    )
    query = apply_support_ticket_scope(query, current_user, db)

    if view == "closed":
        query = query.filter(
            Ticket.status.in_(
                [
                    TicketStatus.resolved,
                    TicketStatus.closed,
                    TicketStatus.canceled,
                ]
            )
        )
    elif view != "all":
        query = query.filter(
            Ticket.status.notin_(
                [
                    TicketStatus.resolved,
                    TicketStatus.closed,
                    TicketStatus.canceled,
                ]
            )
        )

    if view == "needs_human":
        query = query.filter(
            (
                Ticket.conversation_state
                == ConversationState.human_review_required
            )
            | Ticket.required_action.isnot(None)
            | WebchatConversation.handoff_status.in_(
                ["requested", "accepted"]
            )
        )
    elif view == "ai_active":
        query = query.filter(
            WebchatConversation.ai_suspended.is_(False),
            WebchatConversation.active_ai_turn_id.is_not(None),
            WebchatConversation.active_ai_status.in_(
                AI_TURN_TYPING_STATUSES
            ),
        )
    elif view == "mine":
        query = query.filter(
            WebchatConversation.active_agent_id == current_user.id
        )

    if channel and channel != "all":
        query = query.filter(_channel_predicate(channel))

    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            Ticket.ticket_no.ilike(like)
            | Ticket.title.ilike(like)
        )

    rows = (
        query.order_by(
            WebchatConversation.updated_at.desc(),
            WebchatConversation.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return {
        "items": [
            _conversation_out(
                conversation=conversation,
                ticket=ticket,
                last_message=last_message,
                current_user=current_user,
                capabilities=capabilities,
            )
            for conversation, ticket, last_message in rows
        ],
        "source": "nexus_support_conversations",
        "view": view,
    }


@router.get("/resolve")
def resolve_support_conversation(
    session_key: str = Query(..., min_length=1, max_length=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_capability(
        current_user,
        CAP_TICKET_READ,
        db,
        message="support_conversation_read_requires_capability",
    )
    conversation, ticket = _load_conversation(
        db,
        session_key,
        current_user=current_user,
    )
    channel = _channel(conversation, ticket)
    return {
        "conversation": {
            "session_key": _session_key(conversation, ticket),
            "conversation_id": conversation.public_id,
            "ticket_id": ticket.id,
            "handoff_request_id": getattr(
                conversation,
                "current_handoff_request_id",
                None,
            ),
            "channel": channel,
            "source": conversation.origin or channel,
            "pii_minimized": True,
        },
        "ticket": {
            "id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "status": _enum_value(ticket.status),
            "priority": _enum_value(ticket.priority),
        },
        "source": "nexus_support_conversation_resolver",
    }


@router.post("/reply")
def reply_support_conversation(
    payload: SupportConversationReplyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    with managed_session(db):
        conversation, ticket = _load_conversation(
            db,
            payload.session_key,
            current_user=current_user,
        )
        result = admin_reply(
            db,
            ticket.id,
            current_user,
            body=payload.body,
            evidence_reference_id=payload.evidence_reference_id,
            conversation_public_id=conversation.public_id,
        )
    result["session_key"] = _session_key(conversation, ticket)
    result["channel"] = _channel(conversation, ticket)
    message = (
        result.get("message")
        if isinstance(result.get("message"), dict)
        else {}
    )
    metadata = (
        message.get("metadata_json")
        if isinstance(message.get("metadata_json"), dict)
        else {}
    )
    result["message_id"] = message.get("id")
    result["outbound_message_id"] = metadata.get(
        "outbound_message_id"
    )
    return result


@router.get("/metrics")
def support_conversation_metrics(
    since_hours: int = Query(default=24, ge=1, le=720),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_capability(
        current_user,
        CAP_TICKET_READ,
        db,
        message="support_conversation_read_requires_capability",
    )
    since = utc_now() - timedelta(hours=since_hours)
    query = (
        db.query(WebchatConversation, Ticket)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .filter(WebchatConversation.updated_at >= since)
    )
    rows = apply_support_ticket_scope(query, current_user, db).all()
    by_channel: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    needs_human = 0
    ai_active = 0
    for conversation, ticket in rows:
        by_channel[_channel(conversation, ticket)] += 1
        state_counts[_enum_value(ticket.conversation_state)] += 1
        if (
            ticket.conversation_state
            == ConversationState.human_review_required
            or ticket.required_action
            or getattr(conversation, "handoff_status", None)
            in {"requested", "accepted"}
        ):
            needs_human += 1
        if _ai_pending(conversation):
            ai_active += 1
    return {
        "source": "nexus_support_conversations",
        "since_hours": since_hours,
        "total": len(rows),
        "needs_human": needs_human,
        "ai_active": ai_active,
        "by_channel": dict(by_channel),
        "by_state": dict(state_counts),
        "runtime_latency": _runtime_latency_summary(
            db,
            since=since,
            current_user=current_user,
        ),
    }


@router.get("/state")
def support_conversation_state(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_capability(
        current_user,
        CAP_TICKET_READ,
        db,
        message="support_conversation_read_requires_capability",
    )
    open_query = (
        db.query(WebchatConversation.id)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .filter(
            Ticket.status.notin_(
                [
                    TicketStatus.resolved,
                    TicketStatus.closed,
                    TicketStatus.canceled,
                ]
            )
        )
    )
    open_count = apply_support_ticket_scope(
        open_query,
        current_user,
        db,
    ).count()

    requested_query = (
        db.query(WebchatHandoffRequest.id)
        .join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .filter(WebchatHandoffRequest.status == "requested")
    )
    requested_handoffs = apply_support_ticket_scope(
        requested_query,
        current_user,
        db,
    ).count()

    my_query = (
        db.query(WebchatHandoffRequest.id)
        .join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == current_user.id,
        )
    )
    my_handoffs = apply_support_ticket_scope(
        my_query,
        current_user,
        db,
    ).count()
    return {
        "source": "nexus_support_conversations",
        "open": open_count,
        "requested_handoffs": requested_handoffs,
        "my_handoffs": my_handoffs,
        "generated_at": _iso(utc_now()),
    }
