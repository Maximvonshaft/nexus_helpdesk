from __future__ import annotations

from app.enums import SourceChannel
from app.services.outbound_channel_registry import get_outbound_channel_capability
from app.settings import get_settings
from email_test_utils import make_session, ticket, verified_email_account


def test_email_capability_ready_only_when_email_gate_account_and_target_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_OUTBOUND_DISPATCH", raising=False)
    monkeypatch.delenv("OUTBOUND_EMAIL_ENABLED", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    get_settings.cache_clear()
    engine, db = make_session(tmp_path)
    try:
        row = ticket(db)
        cap = get_outbound_channel_capability(SourceChannel.email, db=db, ticket=row)
        assert cap.supports_send is False
        assert "outbound_email_enabled" in cap.missing

        monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
        monkeypatch.setenv("OUTBOUND_EMAIL_ENABLED", "true")
        monkeypatch.setenv("EMAIL_PROVIDER", "ses")
        get_settings.cache_clear()
        verified_email_account(db)

        cap = get_outbound_channel_capability(SourceChannel.email, db=db, ticket=row)
        assert cap.status == "ready"
        assert cap.supports_send is True
    finally:
        db.close()
        engine.dispose()
