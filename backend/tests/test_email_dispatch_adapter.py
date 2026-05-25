from __future__ import annotations

from app.enums import MessageStatus
from app.services.outbound_adapters.email import build_or_get_email_metadata, dispatch_email_outbound
from app.settings import get_settings
from email_test_utils import email_message, make_session, ticket, verified_email_account


class FakeProvider:
    def send_email(self, payload):
        self.payload = payload
        return type("Result", (), {"provider_message_id": "ses-123", "provider_status": "sent_via_fake_ses"})()


def test_email_adapter_dispatches_with_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
    monkeypatch.setenv("OUTBOUND_EMAIL_ENABLED", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    get_settings.cache_clear()
    engine, db = make_session(tmp_path)
    try:
        ticket_row = ticket(db)
        verified_email_account(db)
        message = email_message(db, ticket_row)
        build_or_get_email_metadata(db, message=message, ticket=ticket_row, subject="Update", to_email="alice@example.test")
        provider = FakeProvider()
        status_value, provider_status, sent_at, route = dispatch_email_outbound(db, message=message, ticket=ticket_row, idempotency_key="idem-1", provider=provider)
        assert status_value == MessageStatus.sent
        assert provider_status == "sent_via_fake_ses"
        assert sent_at is not None
        assert route["provider_message_id"] == "ses-123"
        assert provider.payload.subject == "Update"
    finally:
        db.close()
        engine.dispose()
