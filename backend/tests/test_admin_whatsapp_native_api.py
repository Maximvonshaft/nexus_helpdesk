from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_admin_whatsapp_native_api.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api import admin_whatsapp_native  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import ChannelAccount, User  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "admin_whatsapp_native_api.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _user(session, role: UserRole, username: str) -> User:
    row = User(
        username=f"{username}-{_uid()}",
        display_name=username.title(),
        email=f"{username}-{_uid()}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    session.add(row)
    session.flush()
    return row


def _account(session, *, provider: str = "whatsapp", account_id: str = "wa-main") -> ChannelAccount:
    row = ChannelAccount(provider=provider, account_id=account_id, display_name="WhatsApp Main", is_active=True)
    session.add(row)
    session.flush()
    return row


def _snapshot(**overrides):
    data = {
        "account_id": "wa-main",
        "status": "connected",
        "qr_status": "consumed",
        "qr": None,
        "qr_data_url": None,
        "phone_number": "+41790000000",
        "jid": "41790000000@s.whatsapp.net",
        "last_qr_generated_at": None,
        "last_connected_at": "2026-06-12T09:00:00Z",
        "last_disconnected_at": None,
        "last_error_code": None,
        "last_error_message": None,
        "reconnect_count": 0,
    }
    data.update(overrides)
    return SimpleNamespace(as_dict=lambda: dict(data), **data)


def test_admin_can_start_login_and_health_status_is_updated(db_session, monkeypatch):
    admin = _user(db_session, UserRole.admin, "admin")
    account = _account(db_session)
    calls = []

    def fake_call(account_id, action, *, method):
        calls.append((account_id, action, method))
        return _snapshot(account_id=account_id, status="qr_pending", qr_status="pending", qr="qr-string", qr_data_url="data:image/png;base64,abc")

    monkeypatch.setattr(admin_whatsapp_native, "call_whatsapp_sidecar_account_action", fake_call)

    result = admin_whatsapp_native.start_whatsapp_native_login(account.account_id, db=db_session, current_user=admin)

    assert calls == [("wa-main", "start", "POST")]
    assert result.status == "qr_pending"
    assert result.qr_status == "pending"
    assert result.qr == "qr-string"
    assert result.channel_health_status == "degraded"
    db_session.refresh(account)
    assert account.health_status == "degraded"
    assert account.last_health_check_at is not None


def test_admin_can_fetch_whatsapp_native_status(db_session, monkeypatch):
    admin = _user(db_session, UserRole.admin, "admin-status")
    _account(db_session)

    monkeypatch.setattr(admin_whatsapp_native, "call_whatsapp_sidecar_account_action", lambda account_id, action, *, method: _snapshot(account_id=account_id))

    result = admin_whatsapp_native.get_whatsapp_native_status("wa-main", db=db_session, current_user=admin)

    assert result.status == "connected"
    assert result.channel_health_status == "healthy"
    assert result.phone_number == "+41790000000"


def test_non_whatsapp_account_is_not_accepted(db_session, monkeypatch):
    admin = _user(db_session, UserRole.admin, "admin-non-wa")
    _account(db_session, provider="sms", account_id="sms-main")
    monkeypatch.setattr(admin_whatsapp_native, "call_whatsapp_sidecar_account_action", lambda *args, **kwargs: _snapshot())

    with pytest.raises(HTTPException) as exc:
        admin_whatsapp_native.get_whatsapp_native_status("sms-main", db=db_session, current_user=admin)

    assert exc.value.status_code == 404


def test_agent_cannot_manage_whatsapp_native_session(db_session, monkeypatch):
    agent = _user(db_session, UserRole.agent, "agent")
    _account(db_session)
    monkeypatch.setattr(admin_whatsapp_native, "call_whatsapp_sidecar_account_action", lambda *args, **kwargs: _snapshot())

    with pytest.raises(HTTPException) as exc:
        admin_whatsapp_native.restart_whatsapp_native("wa-main", db=db_session, current_user=agent)

    assert exc.value.status_code == 403
