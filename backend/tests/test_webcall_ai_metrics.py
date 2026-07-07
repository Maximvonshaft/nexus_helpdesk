from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_metrics_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User
from app.services.observability import render_prometheus_metrics
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.event_service import write_event
from app.services.webcall_ai_production.metrics import (
    record_webcall_ai_audio,
    record_webcall_ai_stage,
    webcall_ai_metrics_snapshot,
)
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_production_settings.cache_clear()


def _seed_admin(db) -> User:
    user = User(id=9801, username="webcall_ai_metrics_admin", display_name="Metrics Admin", password_hash="test", role=UserRole.admin, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _voice_session(db) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        mode="livekit_ai_agent",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _seed_turn_action(db, session: WebchatVoiceSession) -> None:
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        turn_index=1,
        customer_text_redacted="hello",
        ai_response_text_redacted="Runtime generated reply.",
        language="en",
        intent="tracking_missing_number",
        action="tracking_missing_number",
        handoff_required=False,
        provider="provider_runtime:private_ai_runtime",
        stt_provider="deepgram_streaming",
        tts_provider="cartesia_streaming",
        latency_ms=1234,
        created_at=utc_now(),
    )
    db.add(turn)
    db.flush()
    db.add(
        WebchatVoiceAIAction(
            voice_session_id=session.id,
            turn_id=turn.id,
            model_action="handoff_to_human",
            nexus_decision="handoff",
            decision_reason="barge_in",
            result_status="interrupted",
            created_at=utc_now(),
        )
    )
    db.commit()


def test_webcall_ai_prometheus_metrics_render_without_customer_text():
    record_webcall_ai_stage(stage="llm_decision", status="ok", provider="provider_runtime:private_ai_runtime", elapsed_ms=42)
    record_webcall_ai_audio(provider="cartesia_streaming", status="ok", chunks=2, bytes_count=128)

    rendered = render_prometheus_metrics()

    assert "nexusdesk_webcall_ai_stage_duration_ms_bucket" in rendered
    assert 'stage="llm_decision"' in rendered
    assert 'provider="provider_runtime:private_ai_runtime"' in rendered
    assert "nexusdesk_webcall_ai_audio_chunks_total" in rendered
    assert "Please provide" not in rendered


def test_webcall_ai_snapshot_counts_events_turns_and_handoff():
    db = SessionLocal()
    try:
        session = _voice_session(db)
        duplicate_conversation_session = _voice_session(db)
        duplicate_conversation_session.conversation_id = session.conversation_id
        duplicate_conversation_session.status = "ended"
        duplicate_conversation_session.ended_at = utc_now()
        db.commit()
        _seed_turn_action(db, session)
        write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="webcall_ai.response.spoken", payload={"voice_session_id": session.public_id})
        write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="webcall_ai.response.interrupted", payload={"voice_session_id": session.public_id, "reason": "barge_in"})
        write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="operator.note", payload={"voice_session_id": session.public_id})
        db.commit()

        snapshot = webcall_ai_metrics_snapshot(db)

        assert snapshot["active_sessions"] == 1
        assert snapshot["turn_count"] == 1
        assert snapshot["handoff_count"] == 1
        assert snapshot["spoken_count"] == 1
        assert snapshot["barge_in_count"] == 1
        assert "operator.note" not in snapshot["events_by_type"]
        assert snapshot["provider_rows"][0]["stt_provider"] == "deepgram_streaming"
        assert snapshot["provider_rows"][0]["tts_provider"] == "cartesia_streaming"
    finally:
        db.close()


def test_admin_webcall_ai_health_includes_metrics_snapshot(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_PROFILE", "fake")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    get_webcall_ai_production_settings.cache_clear()
    db = SessionLocal()
    try:
        admin = _seed_admin(db)
        session = _voice_session(db)
        write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="webcall_ai.response.spoken", payload={"voice_session_id": session.public_id})
        db.commit()
    finally:
        db.close()

    response = TestClient(app).get("/api/admin/webcall-ai/health", headers={"Authorization": f"Bearer {create_access_token(admin.id)}"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "metrics" in payload
    assert payload["metrics"]["spoken_count"] >= 1
    assert "events_by_type" in payload["metrics"]


def test_spoken_canary_probe_is_read_only_and_sanitizes_tokens():
    script = (ROOT.parent / "scripts" / "probe_webcall_ai_spoken_canary.sh").read_text(encoding="utf-8")

    assert "/api/admin/webcall-ai/health" in script
    assert "/api/admin/webcall-ai/sessions/${SESSION_PUBLIC_ID}/events" in script
    assert "ADMIN_TOKEN_SET=true" in script
    assert "curl -sS -L" in script
    assert "POST" not in script
    assert "RUN_MUTATING" not in script
