from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_outbound_email_pilot.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api import admin as admin_api  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import OutboundEmailAccount, User  # noqa: E402
from app.services.outbound_email_account_service import count_active_successful_tested_accounts  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pilot.db'}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    return engine, session


def _admin(session) -> User:
    row = User(username="pilot-admin", display_name="Pilot Admin", email="pilot-admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    session.add(row)
    session.flush()
    return row


def _successful_account(session) -> OutboundEmailAccount:
    row = OutboundEmailAccount(
        display_name="Pilot SMTP",
        host="smtp.example.test",
        port=587,
        username="support@example.test",
        password_encrypted="encrypted",
        from_address="support@example.test",
        reply_to="replies@example.test",
        security_mode="starttls",
        is_active=True,
        priority=10,
        health_status="ok",
        last_test_status="success",
        last_test_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def test_outbound_email_pilot_readiness_warns_without_recent_test_send(tmp_path, monkeypatch):
    engine, session = _session(tmp_path)
    try:
        current_user = _admin(session)
        monkeypatch.setattr(admin_api.settings, "outbound_email_production_pilot_enabled", True)
        monkeypatch.setattr(admin_api.settings, "outbound_email_test_send_max_age_hours", 24)

        payload = admin_api.production_readiness(db=session, current_user=current_user)
        signoff = admin_api.signoff_checklist(db=session, current_user=current_user)

        assert payload.outbound_email_production_pilot_enabled is True
        assert payload.outbound_email_successful_test_send_accounts == 0
        assert any("OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true" in item for item in payload.warnings)
        assert signoff["checks"]["outbound_email_pilot_test_send_success"] is False
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_outbound_email_pilot_readiness_passes_with_recent_test_send(tmp_path, monkeypatch):
    engine, session = _session(tmp_path)
    try:
        current_user = _admin(session)
        _successful_account(session)
        monkeypatch.setattr(admin_api.settings, "outbound_email_production_pilot_enabled", True)
        monkeypatch.setattr(admin_api.settings, "outbound_email_test_send_max_age_hours", 24)

        payload = admin_api.production_readiness(db=session, current_user=current_user)
        signoff = admin_api.signoff_checklist(db=session, current_user=current_user)

        assert count_active_successful_tested_accounts(session, max_age_hours=24) == 1
        assert payload.outbound_email_active_accounts == 1
        assert payload.outbound_email_successful_test_send_accounts == 1
        assert not any("OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true requires" in item for item in payload.warnings)
        assert signoff["checks"]["outbound_email_pilot_test_send_success"] is True
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_outbound_email_test_send_gate_and_runbook_are_explicit() -> None:
    repo = ROOT.parent
    script = (repo / "scripts" / "smoke" / "outbound_email_test_send_gate.py").read_text(encoding="utf-8")
    runbook = (repo / "docs" / "runbooks" / "outbound-email-production-pilot.md").read_text(encoding="utf-8")

    assert "I_UNDERSTAND_THIS_SENDS_REAL_EMAIL" in script
    assert "/api/admin/outbound-email/accounts" in script
    assert "/test-send" in script
    assert "last_test_status" in script
    assert "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true" in runbook
