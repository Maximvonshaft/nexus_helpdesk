from __future__ import annotations

from app.models import EmailSuppression
from app.services.email_events import record_email_delivery_event
from email_test_utils import make_session


def test_bounce_event_creates_suppression(tmp_path):
    engine, db = make_session(tmp_path)
    try:
        event = record_email_delivery_event(db, {"eventType": "Bounce", "eventId": "evt-1", "mail": {"messageId": "m-1"}, "bounce": {"bouncedRecipients": [{"emailAddress": "Alice@Example.Test"}]}})
        suppression = db.query(EmailSuppression).filter(EmailSuppression.email_normalized == "alice@example.test").first()
        assert event.event_type == "bounce"
        assert suppression is not None
        assert suppression.reason == "bounce"
    finally:
        db.close()
        engine.dispose()
