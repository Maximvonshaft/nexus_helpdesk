import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_pilot_closure_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.pilot_closure import run_webcall_ai_pilot_closure_once
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceTranscriptSegment


ENV_KEYS = [
    "CI",
    "WEBCALL_AI_AGENT_ENABLED",
    "WEBCALL_AI_STT_RUNTIME_ENABLED",
    "WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED",
    "WEBCALL_AI_ORCHESTRATOR_ENABLED",
    "WEBCALL_AI_TRACKING_REPLY_ENABLED",
    "WEBCALL_AI_TRACKING_LOOKUP_ENABLED",
    "WEBCALL_AI_TTS_RUNTIME_ENABLED",
    "WEBCALL_AI_VOICE_EGRESS_ENABLED",
    "WEBCALL_AI_VOICE_EGRESS_MODE",
    "WEBCALL_AI_PILOT_CLOSURE_ENABLED",
    "WEBCALL_AI_PILOT_MODE",
    "WEBCALL_AI_PILOT_KILL_SWITCH",
    "WEBCALL_AI_PILOT_TENANT_ALLOWLIST",
    "WEBCALL_AI_PILOT_EVIDENCE_ENABLED",
    "WEBCALL_AI_PILOT_HANDOFF_ENABLED",
    "WEBCALL_AI_PILOT_FAKE_TRACKING_ENABLED",
    "WEBCALL_AI_PILOT_FIXTURE_ENABLED",
    "WEBCALL_AI_PILOT_FIXTURE_ALLOW_DB_WRITE",
]


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _enable_common(monkeypatch, mode: str):
    values = {
        "WEBCALL_AI_AGENT_ENABLED": "true",
        "WEBCALL_AI_STT_RUNTIME_ENABLED": "true",
        "WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED": "true",
        "WEBCALL_AI_ORCHESTRATOR_ENABLED": "true",
        "WEBCALL_AI_TRACKING_REPLY_ENABLED": "true",
        "WEBCALL_AI_TTS_RUNTIME_ENABLED": "true",
        "WEBCALL_AI_PILOT_CLOSURE_ENABLED": "true",
        "WEBCALL_AI_PILOT_MODE": mode,
        "WEBCALL_AI_PILOT_KILL_SWITCH": "false",
        "WEBCALL_AI_PILOT_TENANT_ALLOWLIST": "pilot",
        "WEBCALL_AI_PILOT_EVIDENCE_ENABLED": "true",
        "WEBCALL_AI_PILOT_FIXTURE_ENABLED": "true",
        "WEBCALL_AI_PILOT_FIXTURE_ALLOW_DB_WRITE": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_pilot_closure_default_disabled(db):
    result = run_webcall_ai_pilot_closure_once(db, worker_id="worker-a")

    assert result.ok is False
    assert result.error_code == "disabled"


def test_kill_switch_blocks_execution(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_EVIDENCE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_pilot_closure_once(db, worker_id="worker-a")

    assert result.ok is False
    assert result.error_code == "pilot_kill_switch"


def test_no_valid_session_is_not_false_success(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_KILL_SWITCH", "false")
    monkeypatch.setenv("WEBCALL_AI_PILOT_TENANT_ALLOWLIST", "pilot")
    monkeypatch.setenv("WEBCALL_AI_PILOT_EVIDENCE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_pilot_closure_once(db, worker_id="worker-a", tenant_key="pilot")

    assert result.ok is False
    assert result.error_code == "no_session"
    assert result.claimed == 0


def test_simulated_full_loop_uses_fake_tracking_and_builds_evidence(db, monkeypatch):
    _enable_common(monkeypatch, "simulated_full_loop")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_FAKE_TRACKING_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_MODE", "fake_audio_reference")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_pilot_closure_once(db, worker_id="worker-a", tenant_key="pilot")

    assert result.ok is True
    assert result.claimed == 1
    assert result.transcript_segments == 1
    assert result.turns == 1
    assert result.actions == 1
    assert result.tts_runtime_events == 1
    assert result.voice_egress_sent == 1
    assert result.evidence_report == 1
    assert db.query(WebchatVoiceTranscriptSegment).count() == 1


def test_simulated_full_loop_does_not_call_real_tracking_in_ci(db, monkeypatch):
    from app.services.webcall_ai import orchestrator

    _enable_common(monkeypatch, "simulated_full_loop")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_FAKE_TRACKING_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    monkeypatch.setattr(orchestrator, "lookup_tracking_fact", lambda **kwargs: (_ for _ in ()).throw(AssertionError("real lookup")))
    get_webcall_ai_settings.cache_clear()

    assert run_webcall_ai_pilot_closure_once(db, worker_id="worker-a", tenant_key="pilot").ok is True


def test_simulated_full_loop_without_fake_tracking_in_ci_fails_closed(db, monkeypatch):
    _enable_common(monkeypatch, "simulated_full_loop")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_pilot_closure_once(db, worker_id="worker-a", tenant_key="pilot")

    assert result.ok is False
    assert result.error_code == "tracking_unavailable"


def test_handoff_safety_produces_handoff_and_no_voice_egress(db, monkeypatch):
    _enable_common(monkeypatch, "handoff_safety")
    monkeypatch.setenv("WEBCALL_AI_PILOT_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_pilot_closure_once(db, worker_id="worker-a", tenant_key="pilot")
    action = db.query(WebchatVoiceAIAction).one()

    assert result.ok is True
    assert result.handoff_events == 1
    assert result.voice_egress_sent == 0
    assert action.model_action == "handoff_to_human"
    assert action.nexus_decision == "handoff"
    assert action.speedaf_tool_name is None
    assert action.result_status == "handoff_required"
