from __future__ import annotations

from app.api.admin_email import check_readiness, create_email_account
from app.schemas import EmailAccountCreate
from app.settings import get_settings
from email_test_utils import admin, make_session


def test_admin_email_create_and_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
    monkeypatch.setenv("OUTBOUND_EMAIL_ENABLED", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    get_settings.cache_clear()
    engine, db = make_session(tmp_path)
    try:
        user = admin(db)
        created = create_email_account(EmailAccountCreate(account_id="email-main", from_email="support@example.test", verification_status="verified"), db=db, current_user=user)
        readiness = check_readiness(created.id, db=db, current_user=user)
        assert created.provider == "ses"
        assert readiness.ready is True
    finally:
        db.close()
        engine.dispose()
