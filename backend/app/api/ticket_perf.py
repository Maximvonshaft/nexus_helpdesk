from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, inspect, or_, select, text
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..models import (
    Customer,
    MarketBulletin,
    ExternalChannelAttachmentReference,
    ExternalChannelTranscriptMessage,
    Team,
    Ticket,
    TicketAIIntake,
    TicketAttachment,
    TicketComment,
    TicketEvent,
    TicketInternalNote,
    TicketInboundEmailMessage,
    TicketOutboundAttachment,
    TicketOutboundMessage,
    User,
    WhatsAppInboundMessage,
)
from ..services.permissions import ensure_ticket_visible
from ..utils.time import utc_now
from ..webchat_models import WebchatEvent, WebchatMessage
from .deps import get_current_user

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

DEFAULT_TIMELINE_LIMIT = 50
MAX_TIMELINE_LIMIT = 100
SOURCE_ORDER = {
    "comment": 0,
    "internal_note": 1,
    "inbound_email": 2,
    "outbound_message": 3,
    "ai_intake": 4,
    "ticket_event": 5,
    "webchat_event": 6,
    "voice_call": 7,
    "external_channel_transcript": 8,
    "whatsapp_inbound": 9,
}


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _safe_limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_TIMELINE_LIMIT), MAX_TIMELINE_LIMIT))


def _image_thumbnail_data_url(value: Any, mime_type: str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith("data:"):
            return value
        encoded = value.strip()
    elif isinstance(value, list):
        try:
            encoded = base64.b64encode(bytes(int(item) for item in value)).decode("ascii")
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not encoded:
        return None
    return f"data:{mime_type or 'image/jpeg'};base64,{encoded}"


def _whatsapp_media_attachments(row: WhatsAppInboundMessage) -> list[dict[str, Any]]:
    raw = row.raw_payload_json or {}
    raw_payload = raw.get("raw_payload") if isinstance(raw, dict) else None
    source = raw_payload if isinstance(raw_payload, dict) else raw
    message = source.get("message") if isinstance(source, dict) else None
    image = message.get("imageMessage") if isinstance(message, dict) else None
    if not isinstance(image, dict):
        return []
    mime_type = image.get("mimetype") or "image/jpeg"
    thumbnail_url = _image_thumbnail_data_url(image.get("jpegThumbnail"), mime_type)
    return [
        {
            "type": "image",
            "mime_type": mime_type,
            "caption": image.get("caption") or row.body_text or None,
            "width": image.get("width"),
            "height": image.get("height"),
            "thumbnail_url": thumbnail_url,
            "download_url": None,
            "storage_status": "thumbnail_only" if thumbnail_url else "referenced",
            "source": "whatsapp_raw_payload",
        }
    ]


def _customer_summary(row: Customer | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "external_ref": row.external_ref,
    }


def _user_summary(row: User | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "role": _value(row.role),
    }


def _team_summary(row: Team | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": row.id, "name": row.name}


def _load_ticket_tags(db: Session, ticket_id: int) -> list[dict[str, Any]]:
    """Best-effort tag summary without loading legacy detail relationships."""

    try:
        inspector = inspect(db.get_bind())
        tables = set(inspector.get_table_names())
        rows: list[Any] = []
        if "ticket_tags" in tables:
            cols = {item["name"] for item in inspector.get_columns("ticket_tags")}
            if {"ticket_id", "name"}.issubset(cols):
                id_expr = "id" if "id" in cols else "NULL AS id"
                color_expr = "color" if "color" in cols else "NULL AS color"
                rows = db.execute(
                    text(f"SELECT {id_expr}, name, {color_expr} FROM ticket_tags WHERE ticket_id = :ticket_id ORDER BY name ASC"),
                    {"ticket_id": ticket_id},
                ).mappings().all()
            elif {"ticket_id", "tag_id"}.issubset(cols) and "tags" in tables:
                tag_cols = {item["name"] for item in inspector.get_columns("tags")}
                color_expr = "t.color" if "color" in tag_cols else "NULL AS color"
                rows = db.execute(
                    text(
                        f"SELECT t.id, t.name, {color_expr} FROM ticket_tags tt "
                        "JOIN tags t ON t.id = tt.tag_id WHERE tt.ticket_id = :ticket_id ORDER BY t.name ASC"
                    ),
                    {"ticket_id": ticket_id},
                ).mappings().all()
        elif "ticket_tag_links" in tables and "tags" in tables:
            link_cols = {item["name"] for item in inspector.get_columns("ticket_tag_links")}
            tag_cols = {item["name"] for item in inspector.get_columns("tags")}
            if {"ticket_id", "tag_id"}.issubset(link_cols):
                color_expr = "t.color" if "color" in tag_cols else "NULL AS color"
                rows = db.execute(
                    text(
                        f"SELECT t.id, t.name, {color_expr} FROM ticket_tag_links ttl "
                        "JOIN tags t ON t.id = ttl.tag_id WHERE ttl.ticket_id = :ticket_id ORDER BY t.name ASC"
                    ),
                    {"ticket_id": ticket_id},
                ).mappings().all()
        return [{"id": row.get("id"), "name": row.get("name"), "color": row.get("color")} for row in rows]
    except Exception:
        return []


def _counts(db: Session, ticket_id: int) -> dict[str, int]:
    row = db.execute(
        select(
            select(func.count(TicketComment.id)).where(TicketComment.ticket_id == ticket_id).scalar_subquery().label("comments_count"),
            select(func.count(TicketInternalNote.id)).where(TicketInternalNote.ticket_id == ticket_id).scalar_subquery().label("internal_notes_count"),
            select(func.count(TicketAttachment.id)).where(TicketAttachment.ticket_id == ticket_id).scalar_subquery().label("attachments_count"),
            select(func.count(ExternalChannelTranscriptMessage.id)).where(ExternalChannelTranscriptMessage.ticket_id == ticket_id).scalar_subquery().label("external_channel_transcript_count"),
            select(func.count(ExternalChannelAttachmentReference.id)).where(ExternalChannelAttachmentReference.ticket_id == ticket_id).scalar_subquery().label("external_channel_attachment_references_count"),
            select(func.count(TicketOutboundMessage.id)).where(TicketOutboundMessage.ticket_id == ticket_id).scalar_subquery().label("outbound_messages_count"),
            select(func.count(TicketAIIntake.id)).where(TicketAIIntake.ticket_id == ticket_id).scalar_subquery().label("ai_intakes_count"),
            select(func.count(TicketEvent.id)).where(TicketEvent.ticket_id == ticket_id).scalar_subquery().label("events_count"),
        )
    ).mappings().one()
    return {key: int(row[key] or 0) for key in row.keys()}


def _is_overdue(ticket: Ticket) -> bool:
    now = _as_utc(utc_now())
    if now is None:
        return False
    first_due = _as_utc(ticket.first_response_due_at)
    resolution_due = _as_utc(ticket.resolution_due_at)
    return bool(
        (first_due and first_due < now and not ticket.first_response_at)
        or (resolution_due and resolution_due < now and not ticket.resolved_at)
    )


def _attachment_preview(db: Session, ticket_id: int, limit: int = 3) -> list[dict[str, Any]]:
    rows = (
        db.query(TicketAttachment)
        .filter(TicketAttachment.ticket_id == ticket_id)
        .order_by(TicketAttachment.created_at.desc(), TicketAttachment.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "file_name": row.file_name,
            "download_url": row.download_url,
            "mime_type": row.mime_type,
            "file_size": row.file_size,
            "visibility": _value(row.visibility),
            "created_at": _dt(row.created_at),
        }
        for row in rows
    ]


def _attachment_timeline_payload(row: TicketAttachment) -> dict[str, Any]:
    return {
        "id": row.id,
        "file_name": row.file_name,
        "download_url": row.file_url or f"/api/files/{row.id}/download",
        "mime_type": row.mime_type,
        "file_size": row.file_size,
        "visibility": _value(row.visibility),
    }


def _external_channel_transcript_preview(db: Session, ticket_id: int, limit: int = 5) -> list[dict[str, Any]]:
    rows = (
        db.query(ExternalChannelTranscriptMessage)
        .filter(ExternalChannelTranscriptMessage.ticket_id == ticket_id)
        .order_by(ExternalChannelTranscriptMessage.created_at.desc(), ExternalChannelTranscriptMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "role": row.role,
            "author_name": row.author_name,
            "body_text": row.body_text,
            "received_at": _dt(row.received_at),
            "created_at": _dt(row.created_at),
        }
        for row in rows
    ]


def _conversation_transcript_items(db: Session, ticket_id: int, limit: int) -> list[dict[str, Any]]:
    safe_limit = _safe_limit(limit)
    rows: list[dict[str, Any]] = []

    whatsapp_rows = (
        db.query(WhatsAppInboundMessage)
        .filter(WhatsAppInboundMessage.ticket_id == ticket_id)
        .order_by(WhatsAppInboundMessage.received_at.desc(), WhatsAppInboundMessage.id.desc())
        .limit(safe_limit)
        .all()
    )
    for row in whatsapp_rows:
        attachments = _whatsapp_media_attachments(row)
        body = row.body_text or (attachments[0].get("caption") if attachments else None) or ("image message" if attachments else "")
        rows.append(
            {
                "id": f"whatsapp-{row.id}",
                "source_type": "whatsapp_inbound",
                "source_id": row.id,
                "direction": "customer",
                "author_label": row.sender_phone or row.sender_jid,
                "body": body,
                "body_text": body,
                "message_type": row.message_type,
                "message_id": row.external_message_id,
                "chat_jid": row.chat_jid,
                "sender_jid": row.sender_jid,
                "webchat_message_id": row.webchat_message_id,
                "attachments": attachments,
                "created_at": _dt(row.created_at),
                "received_at": _dt(row.received_at),
            }
        )

    if whatsapp_rows:
        outbound_rows = (
            db.query(TicketOutboundMessage)
            .filter(TicketOutboundMessage.ticket_id == ticket_id)
            .order_by(TicketOutboundMessage.created_at.desc(), TicketOutboundMessage.id.desc())
            .limit(safe_limit)
            .all()
        )
        delivered_statuses = {"sent", "delivered", "read"}
        for row in outbound_rows:
            channel = _value(row.channel)
            status_values = {
                str(_value(row.status) or "").lower(),
                str(row.provider_status or "").lower(),
                str(row.delivery_status or "").lower(),
            }
            if channel != "whatsapp" or not (status_values & delivered_statuses):
                continue
            rows.append(
                {
                    "id": f"outbound-{row.id}",
                    "source_type": "outbound_message",
                    "source_id": row.id,
                    "direction": "agent",
                    "author_label": "NexusDesk outbound",
                    "body": row.body,
                    "body_text": row.body,
                    "message_id": row.provider_message_id,
                    "delivery_status": row.delivery_status,
                    "provider_status": row.provider_status,
                    "created_at": _dt(row.created_at),
                    "received_at": _dt(row.sent_at or row.created_at),
                }
            )
    else:
        webchat_rows = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.ticket_id == ticket_id)
            .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
            .limit(safe_limit)
            .all()
        )
        for row in webchat_rows:
            body = getattr(row, "body_text", None) or row.body
            rows.append(
                {
                    "id": f"webchat-{row.id}",
                    "source_type": "webchat_message",
                    "source_id": row.id,
                    "direction": row.direction,
                    "author_label": row.author_label,
                    "body": body,
                    "body_text": body,
                    "message_type": getattr(row, "message_type", None) or "text",
                    "delivery_status": getattr(row, "delivery_status", None),
                    "created_at": _dt(row.created_at),
                    "received_at": _dt(row.created_at),
                }
            )

        external_channel_rows = (
            db.query(ExternalChannelTranscriptMessage)
            .filter(ExternalChannelTranscriptMessage.ticket_id == ticket_id)
            .order_by(
                ExternalChannelTranscriptMessage.received_at.desc().nullslast(),
                ExternalChannelTranscriptMessage.created_at.desc(),
                ExternalChannelTranscriptMessage.id.desc(),
            )
            .limit(safe_limit)
            .all()
        )
        for row in external_channel_rows:
            rows.append(
                {
                    "id": f"external_channel-{row.id}",
                    "source_type": "external_channel_transcript",
                    "source_id": row.id,
                    "direction": row.role,
                    "author_label": row.author_name,
                    "body": row.body_text,
                    "body_text": row.body_text,
                    "message_id": row.message_id,
                    "session_key": row.session_key,
                    "created_at": _dt(row.created_at),
                    "received_at": _dt(row.received_at or row.created_at),
                }
            )

    rows.sort(
        key=lambda item: (
            item.get("received_at") or item.get("created_at") or "",
            SOURCE_ORDER.get(str(item.get("source_type")), 99),
            str(item.get("source_id") or ""),
        )
    )
    return rows[-safe_limit:]


def _external_channel_attachment_preview(db: Session, ticket_id: int, limit: int = 3) -> list[dict[str, Any]]:
    rows = (
        db.query(ExternalChannelAttachmentReference)
        .filter(ExternalChannelAttachmentReference.ticket_id == ticket_id)
        .order_by(ExternalChannelAttachmentReference.created_at.desc(), ExternalChannelAttachmentReference.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "ticket_id": row.ticket_id,
            "transcript_message_id": row.transcript_message_id,
            "remote_attachment_id": row.remote_attachment_id,
            "content_type": row.content_type,
            "filename": row.filename,
            "storage_status": row.storage_status,
            "storage_key": row.storage_key,
            "created_at": _dt(row.created_at),
        }
        for row in rows
    ]


def _effective_bulletin_query(db: Session, ticket: Ticket):
    now = utc_now()
    query = db.query(MarketBulletin).filter(MarketBulletin.is_active.is_(True))
    query = query.filter(or_(MarketBulletin.starts_at.is_(None), MarketBulletin.starts_at <= now))
    query = query.filter(or_(MarketBulletin.ends_at.is_(None), MarketBulletin.ends_at >= now))
    if ticket.market_id is not None:
        query = query.filter(or_(MarketBulletin.market_id.is_(None), MarketBulletin.market_id == ticket.market_id))
    else:
        query = query.filter(MarketBulletin.market_id.is_(None))
    if ticket.country_code:
        query = query.filter(or_(MarketBulletin.country_code.is_(None), MarketBulletin.country_code == ticket.country_code))
    else:
        query = query.filter(MarketBulletin.country_code.is_(None))
    return query


def _active_market_bulletins(db: Session, ticket: Ticket, limit: int = 5) -> list[dict[str, Any]]:
    rows = (
        _effective_bulletin_query(db, ticket)
        .order_by(MarketBulletin.severity.desc(), MarketBulletin.updated_at.desc(), MarketBulletin.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "market_id": row.market_id,
            "country_code": row.country_code,
            "title": row.title,
            "body": row.body,
            "summary": row.summary,
            "category": row.category,
            "channels_csv": row.channels_csv,
            "audience": row.audience,
            "severity": row.severity,
            "auto_inject_to_ai": row.auto_inject_to_ai,
            "is_active": row.is_active,
            "starts_at": _dt(row.starts_at),
            "ends_at": _dt(row.ends_at),
            "created_at": _dt(row.created_at),
            "updated_at": _dt(row.updated_at),
        }
        for row in rows
    ]


@router.get("/{ticket_id}/summary")
def get_ticket_summary(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    result = (
        db.query(Ticket, Customer, User, Team)
        .outerjoin(Customer, Customer.id == Ticket.customer_id)
        .outerjoin(User, User.id == Ticket.assignee_id)
        .outerjoin(Team, Team.id == Ticket.team_id)
        .filter(Ticket.id == ticket_id)
        .first()
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ticket, customer, assignee, team = result
    ensure_ticket_visible(current_user, ticket, db)

    counts = _counts(db, ticket_id)
    attachments = _attachment_preview(db, ticket_id)
    external_channel_transcript = _external_channel_transcript_preview(db, ticket_id)
    external_channel_attachment_references = _external_channel_attachment_preview(db, ticket_id)
    active_market_bulletins = _active_market_bulletins(db, ticket)
    active_market_bulletins_count = _effective_bulletin_query(db, ticket).count()
    latest_ai = (
        db.query(TicketAIIntake)
        .filter(TicketAIIntake.ticket_id == ticket_id)
        .order_by(TicketAIIntake.created_at.desc(), TicketAIIntake.id.desc())
        .first()
    )
    latest_outbound = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket_id)
        .order_by(TicketOutboundMessage.created_at.desc(), TicketOutboundMessage.id.desc())
        .first()
    )
    latest_event = (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket_id)
        .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
        .first()
    )

    payload = {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "description": ticket.description,
        "issue_summary": ticket.issue_summary,
        "status": _value(ticket.status),
        "priority": _value(ticket.priority),
        "source": _value(ticket.source),
        "source_channel": _value(ticket.source_channel),
        "category": ticket.category,
        "sub_category": ticket.sub_category,
        "tracking_number": ticket.tracking_number,
        "case_type": ticket.case_type,
        "customer_request": ticket.customer_request,
        "last_customer_message": ticket.last_customer_message,
        "required_action": ticket.required_action,
        "missing_fields": ticket.missing_fields,
        "customer_update": ticket.customer_update,
        "resolution_summary": ticket.resolution_summary,
        "conversation_state": _value(ticket.conversation_state),
        "created_at": _dt(ticket.created_at),
        "updated_at": _dt(ticket.updated_at),
        "first_response_due_at": _dt(ticket.first_response_due_at),
        "resolution_due_at": _dt(ticket.resolution_due_at),
        "first_response_breached": ticket.first_response_breached,
        "resolution_breached": ticket.resolution_breached,
        "customer": _customer_summary(customer),
        "assignee": _user_summary(assignee),
        "team": _team_summary(team),
        "sla": {
            "overdue": _is_overdue(ticket),
            "first_response_due_at": _dt(ticket.first_response_due_at),
            "resolution_due_at": _dt(ticket.resolution_due_at),
            "first_response_breached": ticket.first_response_breached,
            "resolution_breached": ticket.resolution_breached,
        },
        "tags": _load_ticket_tags(db, ticket_id),
        "counts": counts,
        "evidence_summary": {
            "loaded": True,
            "preview_limit": 5,
            "attachments_count": counts["attachments_count"],
            "external_channel_transcript_count": counts["external_channel_transcript_count"],
            "external_channel_attachment_references_count": counts["external_channel_attachment_references_count"],
            "active_market_bulletins_count": active_market_bulletins_count,
        },
        "latest_ai_summary": latest_ai.summary if latest_ai else ticket.ai_summary,
        "latest_outbound_status": _value(latest_outbound.status) if latest_outbound else None,
        "latest_timeline_event": {
            "id": latest_event.id,
            "event_type": _value(latest_event.event_type),
            "created_at": _dt(latest_event.created_at),
        } if latest_event else None,
        "customer_name": customer.name if customer else None,
        "assignee_name": assignee.display_name if assignee else None,
        "team_name": team.name if team else None,
        "market_code": ticket.market.code if ticket.market else None,
        "country_code": ticket.country_code,
        "ai_summary": ticket.ai_summary,
        "ai_classification": ticket.ai_classification,
        "preferred_reply_channel": ticket.preferred_reply_channel,
        "preferred_reply_contact": ticket.preferred_reply_contact,
        "external_channel_transcript": external_channel_transcript,
        "attachments": attachments,
        "external_channel_attachment_references": external_channel_attachment_references,
        "active_market_bulletins": active_market_bulletins,
    }
    payload.update(counts)
    payload["active_market_bulletins_count"] = active_market_bulletins_count
    return payload


def _encode_timeline_cursor(item: dict[str, Any]) -> str:
    raw = json.dumps(
        {"created_at": item.get("created_at"), "source_type": item.get("source_type"), "source_id": item.get("source_id")},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_sort_key(source_type: str, source_id: int, created_at: datetime) -> tuple[datetime, int, int]:
    return (_as_utc(created_at) or datetime.min.replace(tzinfo=timezone.utc), -SOURCE_ORDER[source_type], int(source_id))


def _parse_cursor(cursor: str | None) -> tuple[datetime, int, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        source_type = str(data["source_type"])
        source_id = int(data["source_id"])
        created = datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))
        if source_type not in SOURCE_ORDER:
            raise ValueError("unknown source_type")
        return _cursor_sort_key(source_type, source_id, created)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor") from exc


def _item_key(item: dict[str, Any]) -> tuple[datetime, int, int]:
    raw_created = item.get("created_at")
    if raw_created:
        created = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
    else:
        created = datetime.min.replace(tzinfo=timezone.utc)
    return _cursor_sort_key(str(item["source_type"]), int(item["source_id"]), created)


def _cursor_predicate(model, source_type: str, cursor_key: tuple[datetime, int, int] | None):
    if cursor_key is None:
        return None

    cursor_created_at, cursor_source_order_key, cursor_source_id = cursor_key
    cursor_source_order = -cursor_source_order_key
    source_order = SOURCE_ORDER[source_type]

    if source_order < cursor_source_order:
        predicate = model.created_at < cursor_created_at
    elif source_order == cursor_source_order:
        predicate = or_(model.created_at < cursor_created_at, and_(model.created_at == cursor_created_at, model.id < cursor_source_id))
    else:
        predicate = or_(model.created_at < cursor_created_at, model.created_at == cursor_created_at)

    return or_(predicate, model.created_at.is_(None))


def _base_timeline_query(query, model, source_type: str, ticket_id: int, cursor_key: tuple[datetime, int, int] | None, limit: int):
    predicate = _cursor_predicate(model, source_type, cursor_key)
    if predicate is not None:
        query = query.filter(predicate)
    return query.filter(model.ticket_id == ticket_id).order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1).all()


def _timeline_items(db: Session, ticket_id: int, cursor_key: tuple[datetime, int, int] | None, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in _base_timeline_query(db.query(TicketComment), TicketComment, "comment", ticket_id, cursor_key, limit):
        items.append({"source_type": "comment", "source_id": row.id, "id": f"comment:{row.id}", "created_at": _dt(row.created_at), "body": row.body, "visibility": _value(row.visibility), "author_id": row.author_id})
    for row in _base_timeline_query(db.query(TicketInternalNote), TicketInternalNote, "internal_note", ticket_id, cursor_key, limit):
        items.append({"source_type": "internal_note", "source_id": row.id, "id": f"internal_note:{row.id}", "created_at": _dt(row.created_at), "body": row.body, "visibility": "internal", "author_id": row.author_id})
    for row in _base_timeline_query(db.query(TicketInboundEmailMessage), TicketInboundEmailMessage, "inbound_email", ticket_id, cursor_key, limit):
        payload = {
            "source": row.source,
            "provider": row.provider,
            "provider_message_id": row.provider_message_id,
            "from_address": row.from_address,
            "from_name": row.from_name,
            "to_address": row.to_address,
            "cc": row.cc,
            "subject": row.subject,
            "body_preview": row.body_preview,
            "mailbox_thread_id": row.mailbox_thread_id,
            "mailbox_message_id": row.mailbox_message_id,
            "mailbox_references": row.mailbox_references,
            "in_reply_to": row.in_reply_to,
            "ticket_event_id": row.ticket_event_id,
            "audit_id": row.audit_id,
            "received_at": _dt(row.received_at),
        }
        items.append(
            {
                "source_type": "inbound_email",
                "source_id": row.id,
                "id": f"inbound_email:{row.id}",
                "created_at": _dt(row.created_at),
                "received_at": _dt(row.received_at),
                "body": row.body,
                "summary": row.body_preview or row.body,
                "subject": row.subject,
                "from_address": row.from_address,
                "from_name": row.from_name,
                "to_address": row.to_address,
                "provider": row.provider,
                "provider_message_id": row.provider_message_id,
                "mailbox_thread_id": row.mailbox_thread_id,
                "mailbox_message_id": row.mailbox_message_id,
                "mailbox_references": row.mailbox_references,
                "in_reply_to": row.in_reply_to,
                "payload": payload,
            }
        )
    outbound_query = db.query(TicketOutboundMessage).options(
        joinedload(TicketOutboundMessage.attachment_links).joinedload(TicketOutboundAttachment.attachment)
    )
    for row in _base_timeline_query(outbound_query, TicketOutboundMessage, "outbound_message", ticket_id, cursor_key, limit):
        attachments = [_attachment_timeline_payload(attachment) for attachment in getattr(row, "attachments", [])]
        payload = {
            "channel": _value(row.channel),
            "status": _value(row.status),
            "provider_status": row.provider_status,
            "provider_message_id": row.provider_message_id,
            "mailbox_thread_id": row.mailbox_thread_id,
            "mailbox_message_id": row.mailbox_message_id,
            "mailbox_references": row.mailbox_references,
            "retry_count": row.retry_count,
            "max_retries": row.max_retries,
            "failure_code": row.failure_code,
            "failure_reason": row.failure_reason,
            "delivery_status": row.delivery_status,
            "delivery_event_type": row.delivery_event_type,
            "delivery_receipt_provider": row.delivery_receipt_provider,
            "delivery_receipt_id": row.delivery_receipt_id,
            "delivery_receipt_at": _dt(row.delivery_receipt_at),
            "delivery_detail": row.delivery_detail,
            "sent_at": _dt(row.sent_at),
            "last_attempt_at": _dt(row.last_attempt_at),
            "next_retry_at": _dt(row.next_retry_at),
            "attachments": attachments,
            "attachment_ids": [attachment["id"] for attachment in attachments],
            "attachments_count": len(attachments),
        }
        items.append({
            "source_type": "outbound_message",
            "source_id": row.id,
            "id": f"outbound_message:{row.id}",
            "created_at": _dt(row.created_at),
            "subject": row.subject,
            "body": row.body,
            "status": _value(row.status),
            "channel": _value(row.channel),
            "created_by": row.created_by,
            "provider_status": row.provider_status,
            "provider_message_id": row.provider_message_id,
            "mailbox_thread_id": row.mailbox_thread_id,
            "mailbox_message_id": row.mailbox_message_id,
            "mailbox_references": row.mailbox_references,
            "retry_count": row.retry_count,
            "max_retries": row.max_retries,
            "failure_code": row.failure_code,
            "failure_reason": row.failure_reason,
            "delivery_status": row.delivery_status,
            "delivery_event_type": row.delivery_event_type,
            "delivery_receipt_provider": row.delivery_receipt_provider,
            "delivery_receipt_id": row.delivery_receipt_id,
            "delivery_receipt_at": _dt(row.delivery_receipt_at),
            "delivery_detail": row.delivery_detail,
            "sent_at": _dt(row.sent_at),
            "last_attempt_at": _dt(row.last_attempt_at),
            "next_retry_at": _dt(row.next_retry_at),
            "attachments": attachments,
            "attachment_ids": payload["attachment_ids"],
            "attachments_count": payload["attachments_count"],
            "payload": payload,
        })
    for row in _base_timeline_query(db.query(TicketAIIntake), TicketAIIntake, "ai_intake", ticket_id, cursor_key, limit):
        items.append({"source_type": "ai_intake", "source_id": row.id, "id": f"ai_intake:{row.id}", "created_at": _dt(row.created_at), "summary": row.summary, "classification": row.classification, "confidence": row.confidence})
    for row in _base_timeline_query(db.query(TicketEvent), TicketEvent, "ticket_event", ticket_id, cursor_key, limit):
        items.append({"source_type": "ticket_event", "source_id": row.id, "id": f"ticket_event:{row.id}", "created_at": _dt(row.created_at), "event_type": _value(row.event_type), "field_name": row.field_name, "note": row.note})
    for row in _base_timeline_query(db.query(WebchatEvent), WebchatEvent, "webchat_event", ticket_id, cursor_key, limit):
        items.append({"source_type": "webchat_event", "source_id": row.id, "id": f"webchat_event:{row.id}", "created_at": _dt(row.created_at), "event_type": row.event_type})
    voice_query = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call")
    voice_predicate = _cursor_predicate(WebchatMessage, "voice_call", cursor_key)
    if voice_predicate is not None:
        voice_query = voice_query.filter(voice_predicate)
    for row in voice_query.order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc()).limit(limit + 1).all():
        try:
            payload = json.loads(row.payload_json or "{}")
        except Exception:
            payload = {"raw": row.payload_json}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        items.append(
            {
                "source_type": "voice_call",
                "kind": "voice_call",
                "source_id": row.id,
                "id": f"voice_call:{row.id}",
                "created_at": _dt(row.created_at),
                "body": row.body_text or row.body,
                "summary": row.body_text or row.body,
                "status": payload.get("status"),
                "payload": payload,
            }
        )
    items.sort(key=_item_key, reverse=True)
    return items


@router.get("/{ticket_id}/timeline")
def get_ticket_timeline_page(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    cursor: str | None = None,
    limit: int = Query(DEFAULT_TIMELINE_LIMIT, ge=1),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    safe_limit = _safe_limit(limit)
    cursor_key = _parse_cursor(cursor)
    rows = _timeline_items(db, ticket_id, cursor_key, safe_limit)
    visible = rows[:safe_limit]
    next_cursor = _encode_timeline_cursor(visible[-1]) if len(rows) > safe_limit and visible else None
    return {"items": visible, "next_cursor": next_cursor, "has_more": bool(next_cursor)}


@router.get("/{ticket_id}/conversation-transcript")
def get_ticket_conversation_transcript(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    limit: int = Query(DEFAULT_TIMELINE_LIMIT, ge=1),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    items = _conversation_transcript_items(db, ticket_id, _safe_limit(limit))
    return {
        "items": items,
        "sources": {
            "webchat": any(item.get("source_type") == "webchat_message" for item in items),
            "external_channel": any(item.get("source_type") == "external_channel_transcript" for item in items),
            "whatsapp": any(item.get("source_type") == "whatsapp_inbound" for item in items),
        },
    }
