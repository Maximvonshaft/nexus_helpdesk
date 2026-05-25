from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EmailWebhookReplay
from ..services.email_events import record_email_delivery_event
from ..services.email_inbound import record_inbound_email
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..utils.time import utc_now

router = APIRouter(prefix="/api/email", tags=["email-integrations"])


def _verify_hmac(db: Session, *, body: bytes, timestamp_header: str | None, signature_header: str | None) -> None:
    settings = get_settings()
    if not settings.email_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="email_webhook_secret_not_configured")
    if not timestamp_header or not signature_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="email_webhook_signature_required")
    try:
        ts = datetime.fromtimestamp(int(timestamp_header), tz=timezone.utc)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_email_webhook_timestamp") from exc
    if abs((utc_now() - ts).total_seconds()) > 300:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="stale_email_webhook_timestamp")
    expected = hmac.new(settings.email_webhook_secret.encode("utf-8"), timestamp_header.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    signature = signature_header.removeprefix("sha256=").strip()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_email_webhook_signature")
    existing = db.query(EmailWebhookReplay).filter(EmailWebhookReplay.signature == signature).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_webhook_replay")
    db.add(EmailWebhookReplay(signature=signature, timestamp=ts))
    cutoff = utc_now() - timedelta(minutes=10)
    db.query(EmailWebhookReplay).filter(EmailWebhookReplay.created_at < cutoff).delete()


@router.post("/webhooks/ses/events")
async def ses_events_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_email_timestamp: str | None = Header(default=None, alias="X-Email-Timestamp"),
    x_email_signature: str | None = Header(default=None, alias="X-Email-Signature"),
):
    body = await request.body()
    with managed_session(db):
        _verify_hmac(db, body=body, timestamp_header=x_email_timestamp, signature_header=x_email_signature)
        payload = await request.json()
        event = record_email_delivery_event(db, payload)
        db.flush()
    return {"ok": True, "event_id": event.id, "event_type": event.event_type}


@router.post("/webhooks/ses/inbound")
async def ses_inbound_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_email_timestamp: str | None = Header(default=None, alias="X-Email-Timestamp"),
    x_email_signature: str | None = Header(default=None, alias="X-Email-Signature"),
):
    body = await request.body()
    with managed_session(db):
        _verify_hmac(db, body=body, timestamp_header=x_email_timestamp, signature_header=x_email_signature)
        payload = await request.json()
        inbound = record_inbound_email(db, payload)
        db.flush()
    return {"ok": True, "inbound_id": inbound.id, "link_status": inbound.link_status, "ticket_id": inbound.ticket_id}
