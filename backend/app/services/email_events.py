from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import EmailDeliveryEvent, EmailOutboundMetadata, EmailSuppression
from ..utils.normalize import normalize_email
from ..utils.time import ensure_utc, utc_now
from .audit_service import log_event


def _event_id(payload: dict[str, Any]) -> str:
    explicit = payload.get("eventId") or payload.get("mail", {}).get("messageId")
    if explicit:
        return str(explicit)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _occurred_at(payload: dict[str, Any]) -> datetime:
    raw = payload.get("timestamp") or payload.get("eventTime") or payload.get("mail", {}).get("timestamp")
    if isinstance(raw, str):
        try:
            return ensure_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except Exception:
            return utc_now()
    return utc_now()


def record_email_delivery_event(db: Session, payload: dict[str, Any]) -> EmailDeliveryEvent:
    event_type = str(payload.get("eventType") or payload.get("notificationType") or "unknown").lower()
    provider_message_id = payload.get("mail", {}).get("messageId") or payload.get("messageId")
    recipient = None
    if event_type in {"bounce", "complaint"}:
        recipients = payload.get("bounce", {}).get("bouncedRecipients") or payload.get("complaint", {}).get("complainedRecipients") or []
        if recipients:
            recipient = recipients[0].get("emailAddress")
    recipient = recipient or (payload.get("mail", {}).get("destination") or [None])[0]
    provider_event_id = _event_id(payload)
    existing = db.query(EmailDeliveryEvent).filter(EmailDeliveryEvent.provider == "ses", EmailDeliveryEvent.provider_event_id == provider_event_id).first()
    if existing is not None:
        return existing
    metadata = None
    if provider_message_id:
        metadata = db.query(EmailOutboundMetadata).filter(EmailOutboundMetadata.provider_message_id == provider_message_id).first()
    event = EmailDeliveryEvent(
        outbound_message_id=metadata.outbound_message_id if metadata else None,
        provider="ses",
        provider_event_id=provider_event_id,
        provider_message_id=provider_message_id,
        event_type=event_type,
        recipient=recipient,
        payload_json=payload,
        occurred_at=_occurred_at(payload),
    )
    db.add(event)
    db.flush()
    if event_type in {"bounce", "complaint"} and recipient:
        normalized = normalize_email(recipient)
        if normalized:
            suppression = db.query(EmailSuppression).filter(EmailSuppression.email_normalized == normalized).first()
            if suppression is None:
                suppression = EmailSuppression(email=recipient, email_normalized=normalized, reason=event_type, source_event_id=event.id, is_active=True)
                db.add(suppression)
            else:
                suppression.reason = event_type
                suppression.source_event_id = event.id
                suppression.is_active = True
            db.flush()
        if event.outbound_message_id:
            log_event(db, ticket_id=metadata.outbound_message.ticket_id, actor_id=None, event_type=EventType.outbound_failed, note=f"Email {event_type} recorded", payload={"email_delivery_event_id": event.id, "recipient": recipient})
    elif event.outbound_message_id and metadata is not None:
        log_event(db, ticket_id=metadata.outbound_message.ticket_id, actor_id=None, event_type=EventType.outbound_sent, note=f"Email {event_type} recorded", payload={"email_delivery_event_id": event.id})
    return event
