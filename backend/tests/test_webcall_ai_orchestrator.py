from uuid import uuid4

import pytest

from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult, safe_tracking_candidate
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.orchestrator import run_webcall_ai_orchestrator
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_AI_ORCHESTRATOR_ENABLED",
        "WEBCALL_AI_TRACKING_LOOKUP_ENABLED",
        "WEBCALL_AI_TRACKING_REPLY_ENABLED",
        "WEBCALL_AI_TRACKING_COUNTRY_CODE",
    ]:
        monkeypatch.delenv(key, raising=False)
    yield
    get_webcall_ai_settings.cache_clear()


def _session() -> WebchatVoiceSession:
    now = utc_now()
    return WebchatVoiceSession(
        id=123,
        public_id=f"voice_{uuid4().hex}",
        conversation_id=11,
        ticket_id=22,
        provider="livekit",
        provider_room_name="room_test",
        status="ringing",
        created_at=now,
        updated_at=now,
    )


def _enable(monkeypatch, *, lookup: bool = False) -> None:
    monkeypatch.setenv("WEBCALL_AI_ORCHESTRATOR_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_TRACKING_REPLY_ENABLED", "true")
    if lookup:
        monkeypatch.setenv("WEBCALL_AI_TRACKING_LOOKUP_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()


def test_tracking_intent_without_number_asks_for_tracking_number(monkeypatch):
    _enable(monkeypatch)

    result = run_webcall_ai_orchestrator(
        customer_text_redacted="Where is my parcel?",
        session=_session(),
        worker_id="worker-a",
    )

    assert result.action == "ask_tracking_number"
    assert result.intent == "tracking_missing_number"
    assert result.tracking_number_hash is None
    assert result.speedaf_tool_name is None


def test_tracking_number_calls_lookup_and_returns_safe_result(monkeypatch):
    _enable(monkeypatch, lookup=True)
    calls = []

    def fake_lookup_tracking_fact(**kwargs):
        calls.append(kwargs)
        return TrackingFactResult(
            ok=True,
            tracking_number=kwargs["tracking_number"],
            status="in_transit",
            status_label="In transit",
            latest_event=TrackingFactEvent(description="Departed facility", location="Zurich"),
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=True,
        )

    monkeypatch.setattr("app.services.webcall_ai.orchestrator.lookup_tracking_fact", fake_lookup_tracking_fact)

    result = run_webcall_ai_orchestrator(
        customer_text_redacted="Track SF123456789CN please",
        session=_session(),
        worker_id="worker-a",
    )

    assert calls[0]["tracking_number"] == "SF123456789CN"
    assert calls[0]["country_code"] == "CH"
    assert result.action == "explain_tracking_fact"
    assert result.tracking_fact_ok is True
    assert result.speedaf_tool_name == "speedaf.order.query"
    assert result.tracking_number_hash and result.tracking_number_hash.startswith("sha256:")
    assert result.tracking_number_suffix == "89CN"
    assert "SF123456789CN" not in result.ai_response_text_redacted


def test_multiple_candidates_asks_for_suffixes_only(monkeypatch):
    _enable(monkeypatch, lookup=True)

    def fake_lookup_tracking_fact(**kwargs):
        return TrackingFactResult(
            ok=False,
            tracking_number=kwargs["tracking_number"],
            tool_status="multiple",
            failure_reason="multiple_waybill_candidates",
            safe_candidates=[
                safe_tracking_candidate("SF123456789CN", suffix="6789"),
                safe_tracking_candidate("SF987654321CN", suffix="4321"),
            ],
        )

    monkeypatch.setattr("app.services.webcall_ai.orchestrator.lookup_tracking_fact", fake_lookup_tracking_fact)

    result = run_webcall_ai_orchestrator(
        customer_text_redacted="Track SF123456789CN",
        session=_session(),
        worker_id="worker-a",
    )

    assert result.action == "ask_waybill_suffix_selection"
    assert "6789" in result.ai_response_text_redacted
    assert "4321" in result.ai_response_text_redacted
    assert "SF123456789CN" not in result.ai_response_text_redacted


def test_lookup_disabled_does_not_call_tracking_service(monkeypatch):
    _enable(monkeypatch, lookup=False)
    monkeypatch.setattr(
        "app.services.webcall_ai.orchestrator.lookup_tracking_fact",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("lookup should not be called")),
    )

    result = run_webcall_ai_orchestrator(
        customer_text_redacted="Track SF123456789CN",
        session=_session(),
        worker_id="worker-a",
    )

    assert result.action == "handoff_to_human"
    assert result.handoff_required is True
    assert result.tracking_failure_reason == "tracking_lookup_disabled"
    assert result.speedaf_tool_name is None


@pytest.mark.parametrize(
    "text",
    [
        "Cancel this order SF123456789CN",
        "I want a refund for SF123456789CN",
        "Change address for SF123456789CN",
        "The driver caused this complaint",
        "This is a legal privacy issue",
    ],
)
def test_forbidden_text_handoffs_without_lookup(monkeypatch, text):
    _enable(monkeypatch, lookup=True)
    monkeypatch.setattr(
        "app.services.webcall_ai.orchestrator.lookup_tracking_fact",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("lookup should not be called")),
    )

    result = run_webcall_ai_orchestrator(customer_text_redacted=text, session=_session(), worker_id="worker-a")

    assert result.action == "handoff_to_human"
    assert result.nexus_decision == "handoff"
    assert result.speedaf_tool_name is None


def test_lookup_error_does_not_invent_status(monkeypatch):
    _enable(monkeypatch, lookup=True)

    def boom(**kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr("app.services.webcall_ai.orchestrator.lookup_tracking_fact", boom)

    result = run_webcall_ai_orchestrator(
        customer_text_redacted="Track SF123456789CN",
        session=_session(),
        worker_id="worker-a",
    )

    assert result.action == "handoff_to_human"
    assert result.tracking_failure_reason == "tracking_lookup_error"
    assert "delivered" not in result.ai_response_text_redacted.lower()
    assert result.speedaf_tool_name == "speedaf.order.query"
