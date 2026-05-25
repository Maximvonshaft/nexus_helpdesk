from __future__ import annotations

from app.models import ChannelAccount
from app.services.channel_account_registry import resolve_channel_account_for_provider
from app.services.openclaw_bridge import resolve_channel_account
from email_test_utils import make_session, uid


def test_provider_scoped_resolver_never_returns_email_for_whatsapp(tmp_path):
    engine, db = make_session(tmp_path)
    try:
        db.add(ChannelAccount(provider="email", account_id=f"email-{uid()}", is_active=True, priority=1))
        db.add(ChannelAccount(provider="whatsapp", account_id=f"wa-{uid()}", is_active=True, priority=10))
        db.flush()
        assert resolve_channel_account_for_provider(db, provider="whatsapp").provider == "whatsapp"
        assert resolve_channel_account(db, market_id=None, account_id=None).provider == "whatsapp"
    finally:
        db.close()
        engine.dispose()
