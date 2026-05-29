from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_realtime_health_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.webchat_events import admin_webchat_realtime_health, _hash_token  # noqa: E402
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base  # noqa: E402
from app.db import get_db  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Ticket, User, UserCapabilityOverride  # noqa: E402
from app.services.permissions import CAP_WEBCHAT_REALTIME_MONITOR  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_WS_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_WS_ADMIN_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_WS_PUBLIC_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_WS_BROKER", "database")
    monkeypatch.setenv("WEBCHAT_WS_REPLAY_POLL_MS", "750")
    monkeypatch.setenv("WEBCHAT_WS_FALLBACK_POLL_MS", "5000")
    monkeypatch.setenv("WEBCHAT_WS_HEARTBEAT_MS", "30000")
    get_settings.cache_clear()
    db_file = tmp_path / "webchat_realtime_health.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture()
def client(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db, username: str, role: UserRole) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _seed_event(db) -> WebchatEvent:
    ticket = Ticket(
        ticket_no="RT-1",
        title="Realtime health",
        description="Realtime health",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id="wc-realtime-health",
        visitor_token_hash=_hash_token("visitor-token"),
        tenant_key="default",
        channel_key="webchat",
        ticket_id=ticket.id,
    )
    db.add(conversation)
    db.flush()
    event = WebchatEvent(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload_json='{"ok":true}',
    )
    db.add(event)
    db.flush()
    return event


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_realtime_health_endpoint_uses_real_route_and_auth(client, db_session):
    auditor = _user(db_session, "auditor", UserRole.auditor)
    event = _seed_event(db_session)

    response = client.get("/api/webchat/admin/realtime-health", headers=_headers(auditor))

    assert response.status_code == 200
    body = response.json()
    assert body["ws_path"] == "/api/webchat/ws"
    assert body["broker"]["name"] == "database"
    assert body["events"]["last_event_id"] == event.id


def test_realtime_health_reports_runtime_contract_from_real_backend_state(db_session):
    lead = _user(db_session, "lead", UserRole.lead)
    event = _seed_event(db_session)

    result = asyncio.run(admin_webchat_realtime_health(db=db_session, current_user=lead))

    assert result["enabled"] is True
    assert result["admin_enabled"] is True
    assert result["public_enabled"] is False
    assert result["ws_path"] == "/api/webchat/ws"
    assert result["broker"] == {"name": "database", "durable_replay": True, "cross_worker_safe": True}
    assert result["replay_poll_ms"] == 750
    assert result["fallback_poll_ms"] == 5000
    assert result["heartbeat_ms"] == 30000
    assert result["events"]["last_event_id"] == event.id
    assert result["events"]["recent_event_count"] == 1
    assert result["events"]["event_types"] == {"message.created": 1}
    assert result["hub"]["connections"] >= 0
    assert result["warnings"] == ["webchat_ws_public_disabled"]


def test_realtime_health_requires_realtime_monitor_capability(db_session):
    manager = _user(db_session, "manager", UserRole.manager)
    db_session.add(UserCapabilityOverride(user_id=manager.id, capability=CAP_WEBCHAT_REALTIME_MONITOR, allowed=False))
    db_session.flush()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_webchat_realtime_health(db=db_session, current_user=manager))

    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_realtime_monitor_requires_capability"
