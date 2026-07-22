from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/webchat_voice_compensation_tests.db",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import ChannelAccount, Market, Tenant
from app.models_agent_routing import ConversationControl
from app.services.livekit_voice_provider import LiveKitVoiceProvider
from app.services.voice_provider import VoiceProviderError
from app.voice_models import VoiceChannelConfiguration
from app.webchat_models import WebchatConversation


@pytest.fixture(autouse=True)
def isolated_schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def voice_env(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "livekit")
    monkeypatch.setenv(
        "WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES",
        "/webchat/voice,/webcall",
    )
    monkeypatch.setenv("LIVEKIT_URL", "wss://voice.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit_secret")
    monkeypatch.setenv(
        "WEBCHAT_VOICE_CONNECT_SRC",
        "wss://voice.example.test https://voice.example.test",
    )
    yield


def _seed_voice_channel() -> None:
    db = SessionLocal()
    try:
        tenant = Tenant(
            tenant_key="pytest-voice-compensation",
            display_name="Voice Compensation",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        market = Market(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="test",
            code="ME-COMPENSATION",
            name="Montenegro Compensation",
            country_code="ME",
            is_active=True,
        )
        db.add(market)
        db.flush()
        account = ChannelAccount(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="test",
            provider="voice",
            account_id="+38220000999",
            display_name="Compensation Voice",
            market_id=market.id,
            is_active=True,
            health_status="configured",
        )
        db.add(account)
        db.flush()
        db.add(
            VoiceChannelConfiguration(
                channel_account_id=account.id,
                inbound_trunk_id="ST_COMPENSATION",
                outbound_trunk_id="ST_COMPENSATION_OUT",
                dispatch_rule_id="SDR_COMPENSATION",
                routing_mode="human_first",
                ai_agent_name="nexus-voice-controller",
                queue_timeout_seconds=90,
                offer_timeout_seconds=20,
                wrap_up_seconds=30,
                overflow_action="ai",
                voicemail_enabled=False,
                recording_policy="disabled",
                transcription_policy="disabled",
                enabled=True,
            )
        )
        db.commit()
    finally:
        db.close()


def _create_webchat_conversation(client: TestClient) -> tuple[str, str]:
    _seed_voice_channel()
    init = client.post(
        "/api/webchat/init",
        json={
            "tenant_key": "pytest-voice-compensation",
            "channel_key": "website",
            "visitor_name": "Compensation Visitor",
            "page_url": "https://example.test/help",
        },
    )
    assert init.status_code == 200, init.text
    payload = init.json()
    return payload["conversation_id"], payload["visitor_token"]


def test_livekit_room_is_closed_when_token_issuance_fails(monkeypatch):
    created_rooms: list[str] = []
    closed_rooms: list[str] = []

    def fake_create_room(self, *, room_name: str) -> str:
        created_rooms.append(room_name)
        return room_name

    def fake_close_room(self, *, room_name: str) -> None:
        closed_rooms.append(room_name)
        return None

    def fake_issue_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        ttl_seconds: int,
    ):
        raise VoiceProviderError("simulated token issuance failure")

    monkeypatch.setattr(LiveKitVoiceProvider, "create_room", fake_create_room)
    monkeypatch.setattr(LiveKitVoiceProvider, "close_room", fake_close_room)
    monkeypatch.setattr(
        LiveKitVoiceProvider,
        "issue_participant_token",
        fake_issue_token,
    )

    client = TestClient(app, raise_server_exceptions=False)
    conversation_id, visitor_token = _create_webchat_conversation(client)
    db = SessionLocal()
    try:
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .one()
        )
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .one()
        )
        control.country_code = "ME"
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert response.status_code >= 500, response.text
    assert len(created_rooms) == 1
    assert closed_rooms == created_rooms
    assert created_rooms[0].startswith("nexus_voice_wv_")
