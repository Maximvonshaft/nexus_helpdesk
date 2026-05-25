from __future__ import annotations

from app.enums import MessageStatus, SourceChannel
from app.models import EmailOutboundMetadata
from app.schemas import OutboundSendRequest
from app.services.ticket_service import send_outbound_message
from app.settings import get_settings
from email_test_utils import admin, make_session, ticket, verified_email_account


def test_email_send_request_creates_metadata_and_pending_row(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
    monkeypatch.setenv("OUTBOUND_EMAIL_ENABLED", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    get_settings.cache_clear()
    engine, db = make_session(tmp_path)
    try:
        user = admin(db)
        row = ticket(db)
        verified_email_account(db)
        message = send_outbound_message(db, row.id, OutboundSendRequest(channel=SourceChannel.email, body="hello", email_to="alice@example.test", email_subject="Case update"), user)
        metadata = db.query(EmailOutboundMetadata).filter(EmailOutboundMetadata.outbound_message_id == message.id).first()
        assert message.status == MessageStatus.pending
        assert metadata.subject == "Case update"
        assert metadata.to_email == "alice@example.test"
    finally:
        db.close()
        engine.dispose()
