from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import EmailInboundMessage, EmailOutboundMetadata
from ..utils.time import utc_now
from .audit_service import log_event
from .email_security import normalize_email_address


TOKEN_RE = re.compile(r"\+nx-([a-f0-9]{16,80})@", re.IGNORECASE)


def extract_reply_token(*, to_email: str | None, headers: dict[str, Any] | None = None) -> str | None:
    values = [to_email or ""]
    if headers:
        values.extend(str(v) for v in headers.values())
    for value in values:
        match = TOKEN_RE.search(value)
        if match:
            return match.group(1).lower()
    return None


def record_inbound_email(db: Session, payload: dict[str, Any]) -> EmailInboundMessage:
    provider_message_id = str(payload.get("messageId") or payload.get("mail", {}).get("messageId") or "")
    if not provider_message_id:
        raise ValueError("missing_provider_message_id")
    existing = db.query(EmailInboundMessage).filter(EmailInboundMessage.provider == "ses", EmailInboundMessage.provider_message_id == provider_message_id).first()
    if existing is not None:
        return existing
    from_email = normalize_email_address(payload.get("from") or payload.get("source") or payload.get("mail", {}).get("source"))
    destinations = payload.get("to") or payload.get("destination") or payload.get("mail", {}).get("destination") or []
    if isinstance(destinations, str):
        destinations = [destinations]
    to_email = normalize_email_address(destinations[0] if destinations else None)
    token = extract_reply_token(to_email=to_email, headers=payload.get("headers") or {})
    metadata = db.query(EmailOutboundMetadata).filter(EmailOutboundMetadata.reply_token == token).first() if token else None
    link_status = "linked" if metadata is not None else "manual_review"
    inbound = EmailInboundMessage(
        ticket_id=metadata.outbound_message.ticket_id if metadata else None,
        provider="ses",
        provider_message_id=provider_message_id,
        from_email=from_email or "",
        to_email=to_email or "",
        subject=payload.get("subject"),
        body_text=payload.get("bodyText") or payload.get("text"),
        reply_token=token,
        link_status=link_status,
        payload_json=payload,
        received_at=utc_now(),
    )
    db.add(inbound)
    db.flush()
    if metadata is not None:
        log_event(db, ticket_id=metadata.outbound_message.ticket_id, actor_id=None, event_type=EventType.comment_added, note="Inbound Email reply linked deterministically", payload={"email_inbound_message_id": inbound.id, "reply_token": token})
    return inbound
