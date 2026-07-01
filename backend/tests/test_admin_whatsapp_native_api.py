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
from app.services import whatsapp_native_admin as whatsapp_native_admin_service  # noqa: E402
from app.settings import get_settings  # noqa: E402


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
        "last_transport_at": "2026-06-12T09:00:01Z",
        "last_qr_expires_at": None,
        "session_state": "linked",
        "browser": ["Ubuntu", "NexusDesk", "22.04.4"],
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
    assert result.session_state == "linked"
    assert result.browser == ["Ubuntu", "NexusDesk", "22.04.4"]
    assert result.last_transport_at == "2026-06-12T09:00:01Z"


def test_admin_can_request_pairing_code_without_leaking_full_phone(db_session, monkeypatch):
    admin = _user(db_session, UserRole.admin, "admin-pairing")
    _account(db_session)
    calls = []

    class Result:
        def as_dict(self):
            return {
                "ok": True,
                "account_id": "wa-main",
                "pairing_code": "12A44SCH",
                "phone_number_suffix": "9737",
                "error_code": None,
                "retryable": None,
            }

    def fake_pairing(account_id, phone_number):
        calls.append((account_id, phone_number))
        return Result()

    monkeypatch.setattr(admin_whatsapp_native, "request_whatsapp_sidecar_pairing_code", fake_pairing)

    result = admin_whatsapp_native.request_whatsapp_native_pairing_code(
        "wa-main",
        admin_whatsapp_native.WhatsAppNativePairingCodeRequest(phone_number="+41 79 855 97 37"),
        db=db_session,
        current_user=admin,
    )

    assert calls == [("wa-main", "+41 79 855 97 37")]
    assert result.ok is True
    assert result.pairing_code == "12A44SCH"
    assert result.phone_number_suffix == "9737"
    assert "+41798559737" not in result.model_dump_json()


def test_pairing_code_service_sanitizes_phone_before_calling_sidecar(monkeypatch):
    monkeypatch.setenv("WHATSAPP_NATIVE_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_SIDECAR_URL", "http://sidecar.test")
    monkeypatch.setenv("WHATSAPP_SIDECAR_TOKEN", "unit-token")
    monkeypatch.setenv("WHATSAPP_SIDECAR_TIMEOUT_SECONDS", "9")
    get_settings.cache_clear()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "account_id": "wa-main",
                "pairing_code": "12A44SCH",
                "phone_number_suffix": "9737",
            }

    class Client:
        def __init__(self):
            self.calls = []

        def post(self, url, *, headers, timeout, json=None):
            self.calls.append((url, headers, timeout, json))
            return Response()

    client = Client()
    try:
        result = whatsapp_native_admin_service.request_whatsapp_sidecar_pairing_code(
            "wa-main",
            "+41 79 855 97 37",
            client=client,
        )
    finally:
        get_settings.cache_clear()

    assert client.calls == [
        (
            "http://sidecar.test/accounts/wa-main/pairing-code",
            {"Authorization": "Bearer unit-token"},
            9.0,
            {"phone_number": "41798559737"},
        )
    ]
    assert result.ok is True
    assert result.phone_number_suffix == "9737"
    assert result.pairing_code == "12A44SCH"
    assert "41798559737" not in str(result.as_dict())


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
