import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_pilot_gate_tests.db")

import pytest

from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.pilot_canary_gate import evaluate_webcall_ai_pilot_gate
from app.voice_models import WebchatVoiceSession


PILOT_ENV_KEYS = [
    "APP_ENV",
    "WEBCALL_AI_PILOT_CLOSURE_ENABLED",
    "WEBCALL_AI_PILOT_KILL_SWITCH",
    "WEBCALL_AI_PILOT_SESSION_ALLOWLIST",
    "WEBCALL_AI_PILOT_TENANT_ALLOWLIST",
    "WEBCALL_AI_PILOT_CANARY_PERCENT",
    "WEBCALL_AI_PILOT_EVIDENCE_ENABLED",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in PILOT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    get_webcall_ai_settings.cache_clear()


def _settings(monkeypatch, **values):
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_KILL_SWITCH", "false")
    monkeypatch.setenv("WEBCALL_AI_PILOT_EVIDENCE_ENABLED", "true")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_webcall_ai_settings.cache_clear()
    return get_webcall_ai_settings()


def test_default_disabled_and_kill_switch_block(monkeypatch):
    assert evaluate_webcall_ai_pilot_gate(session=None).reason == "pilot_disabled"

    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_PILOT_EVIDENCE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    decision = evaluate_webcall_ai_pilot_gate(session=None)
    assert decision.allowed is False
    assert decision.reason == "pilot_kill_switch"


def test_session_and_tenant_allowlists_allow(monkeypatch):
    settings = _settings(monkeypatch, WEBCALL_AI_PILOT_SESSION_ALLOWLIST="voice_allowed")
    session = WebchatVoiceSession(public_id="voice_allowed", conversation_id=1, ticket_id=1)

    assert evaluate_webcall_ai_pilot_gate(session=session, settings=settings).allowed is True

    settings = _settings(monkeypatch, WEBCALL_AI_PILOT_TENANT_ALLOWLIST="pilot")
    decision = evaluate_webcall_ai_pilot_gate(session=None, tenant_key="pilot", settings=settings)
    assert decision.allowed is True
    assert decision.reason == "tenant_allowlist"


def test_canary_is_deterministic_and_bounded(monkeypatch):
    settings = _settings(monkeypatch, WEBCALL_AI_PILOT_CANARY_PERCENT="1")

    first = evaluate_webcall_ai_pilot_gate(session=None, tenant_key="tenant-a", settings=settings)
    second = evaluate_webcall_ai_pilot_gate(session=None, tenant_key="tenant-a", settings=settings)

    assert first == second
    assert first.allowed is True


def test_production_rejected_by_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PILOT_CLOSURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PILOT_CLOSURE_ENABLED"):
        get_webcall_ai_settings()
