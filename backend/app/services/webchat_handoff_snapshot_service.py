from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import BackgroundJob, Ticket, TicketEvent
from ..utils.time import utc_now
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
    return {
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


def enqueue_webchat_handoff_snapshot_job(db: Session, *, snapshot: dict[str, Any]) -> BackgroundJob:
    dedupe_key = "webchat-fast-handoff:{tenant}:{session}:{client}".format(
        tenant=snapshot.get("tenant_key") or "default",
        session=snapshot.get("session_id") or "unknown",
        client=snapshot.get("client_message_id") or "unknown",
    )
    return enqueue_background_job(
        db,
        queue_name="webchat_handoff_snapshot",
        job_type=WEBCHAT_HANDOFF_SNAPSHOT_JOB,
        payload={"snapshot": snapshot},
        dedupe_key=dedupe_key,
    )


def create_ticket_from_webchat_snapshot(db: Session, *, snapshot: dict[str, Any]) -> Ticket:
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
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number=snapshot.get("tracking_number"),
        source_chat_id=f"webchat-fast:{snapshot.get('tenant_key') or 'default'}:{snapshot.get('session_id') or 'unknown'}"[:120],
        customer_request=snapshot.get("customer_last_message"),
        last_customer_message=snapshot.get("customer_last_message"),
        required_action=snapshot.get("recommended_agent_action"),
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=(snapshot.get("visitor") or {}).get("email") or (snapshot.get("visitor") or {}).get("phone") or snapshot.get("session_id"),
    )
    db.add(ticket)
    db.flush()
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.ticket_created,
        note="WebChat AI handoff snapshot created",
        payload_json=json.dumps(snapshot, ensure_ascii=False),
    ))
    db.flush()
    return ticket


def process_webchat_handoff_snapshot_job(db: Session, *, snapshot: dict[str, Any]) -> dict[str, Any]:
    ticket = create_ticket_from_webchat_snapshot(db, snapshot=snapshot)
    return {"status": "done", "ticket_id": ticket.id, "ticket_no": ticket.ticket_no}
