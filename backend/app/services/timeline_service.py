from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from ..enums import EventType, MessageStatus
from ..models import Ticket, TicketAIIntake, TicketAttachment, TicketComment, TicketEvent, TicketInternalNote, TicketOutboundMessage


def _actor_name(obj, fallback: Optional[str] = None) -> Optional[str]:
    actor = getattr(obj, "actor", None) or getattr(obj, "author", None) or getattr(obj, "creator", None) or getattr(obj, "uploader", None)
    if actor:
        return actor.display_name
    return fallback


def serialize_event(event: TicketEvent) -> dict:
    payload = {}
    if event.payload_json:
        try:
            payload = json.loads(event.payload_json)
        except Exception:
            payload = {"raw": event.payload_json}

    title = event.event_type.value.replace("_", " ").title()
    summary = event.note or ""
    if event.event_type == EventType.status_changed:
        title = "Status changed"
        summary = f"Status changed from {event.old_value} to {event.new_value}"
    elif event.event_type == EventType.assigned:
        title = "Ticket assigned"
        summary = payload.get("summary") or f"Assigned to {payload.get('assignee_name', 'unassigned')}"
    elif event.event_type == EventType.escalated:
        title = "Ticket escalated"
        summary = payload.get("summary") or f"Escalated to {payload.get('team_name', 'new team')}"
    elif event.event_type == EventType.reopened:
        title = "Ticket reopened"
        summary = payload.get("summary") or "Ticket reopened for further processing"
    elif event.event_type == EventType.field_updated:
        title = "Field updated"
        summary = payload.get("summary") or f"{event.field_name} updated"

    return {
        "id": f"event-{event.id}",
        "kind": "event",
        "title": title,
        "summary": summary,
        "visibility": "internal",
        "actor_id": event.actor_id,
        "actor_display_name": _actor_name(event),
        "created_at": event.created_at,
        "payload": payload,
    }


def serialize_comment(comment: TicketComment) -> dict:
    return {
        "id": f"comment-{comment.id}",
        "kind": "comment",
        "title": "Customer-facing comment",
        "summary": comment.body,
        "visibility": comment.visibility.value,
        "actor_id": comment.author_id,
        "actor_display_name": _actor_name(comment),
        "created_at": comment.created_at,
        "payload": {"visibility": comment.visibility.value},
    }


def serialize_note(note: TicketInternalNote) -> dict:
    return {
        "id": f"note-{note.id}",
        "kind": "internal_note",
        "title": "Internal note added",
        "summary": note.body,
        "visibility": "internal",
        "actor_id": note.author_id,
        "actor_display_name": _actor_name(note),
        "created_at": note.created_at,
        "payload": {},
    }


def serialize_attachment(attachment: TicketAttachment) -> dict:
    return {
        "id": f"attachment-{attachment.id}",
        "kind": "attachment",
        "title": "Attachment uploaded",
        "summary": attachment.file_name,
        "visibility": attachment.visibility.value,
        "actor_id": attachment.uploaded_by,
        "actor_display_name": _actor_name(attachment),
        "created_at": attachment.created_at,
        "payload": {
            "file_name": attachment.file_name,
            "download_url": attachment.file_url or f"/api/files/{attachment.id}/download",
            "visibility": attachment.visibility.value,
        },
    }


def serialize_outbound(message: TicketOutboundMessage) -> dict:
    if message.status == MessageStatus.draft:
        title = "Reply draft saved"
        summary = f"Draft saved for {message.channel.value}"
    elif message.status == MessageStatus.pending:
        title = "Reply queued"
        summary = f"Reply queued for {message.channel.value}"
    elif message.status == MessageStatus.processing:
        title = "Reply dispatch in progress"
        summary = f"Dispatch worker is sending via {message.channel.value}"
    elif message.status == MessageStatus.sent:
        title = "Reply sent"
        summary = f"Reply sent via {message.channel.value}"
    elif message.status == MessageStatus.dead:
        title = "Reply permanently failed"
        summary = message.failure_reason or message.error_message or f"Send permanently failed via {message.channel.value}"
    else:
        title = "Reply send failed"
        summary = message.error_message or f"Send failed via {message.channel.value}"

    return {
        "id": f"outbound-{message.id}",
        "kind": "outbound",
        "title": title,
        "summary": summary,
        "visibility": "internal",
        "actor_id": message.created_by,
        "actor_display_name": _actor_name(message),
        "created_at": message.created_at,
        "payload": {
            "channel": message.channel.value,
            "status": message.status.value,
            "provider_status": message.provider_status,
        },
    }


def serialize_ai_intake(ai_intake: TicketAIIntake) -> dict:
    confidence = f"{round((ai_intake.confidence or 0) * 100)}%" if ai_intake.confidence is not None else "n/a"
    return {
        "id": f"ai-{ai_intake.id}",
        "kind": "ai_intake",
        "title": "AI intake captured",
        "summary": f"{ai_intake.classification or 'Unclassified'} · confidence {confidence}",
        "visibility": "internal",
        "actor_id": ai_intake.created_by,
        "actor_display_name": _actor_name(ai_intake, "AI Assistant"),
        "created_at": ai_intake.created_at,
        "payload": {
            "summary": ai_intake.summary,
            "classification": ai_intake.classification,
            "confidence": ai_intake.confidence,
            "recommended_action": ai_intake.recommended_action,
            "suggested_reply": ai_intake.suggested_reply,
            "human_override_reason": ai_intake.human_override_reason,
        },
    }


def build_unified_timeline(db: Session, ticket_id: int) -> list[dict]:
    ticket = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.events).joinedload(TicketEvent.actor),
            joinedload(Ticket.comments).joinedload(TicketComment.author),
            joinedload(Ticket.internal_notes).joinedload(TicketInternalNote.author),
            joinedload(Ticket.attachments).joinedload(TicketAttachment.uploader),
            joinedload(Ticket.outbound_messages).joinedload(TicketOutboundMessage.creator),
            joinedload(Ticket.ai_intakes).joinedload(TicketAIIntake.creator),
        )
        .filter(Ticket.id == ticket_id)
        .first()
    )
    if not ticket:
        return []

    items = []
    items.extend(serialize_event(x) for x in ticket.events)
    items.extend(serialize_comment(x) for x in ticket.comments)
    items.extend(serialize_note(x) for x in ticket.internal_notes)
    items.extend(serialize_attachment(x) for x in ticket.attachments)
    items.extend(serialize_outbound(x) for x in ticket.outbound_messages)
    items.extend(serialize_ai_intake(x) for x in ticket.ai_intakes)
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items
