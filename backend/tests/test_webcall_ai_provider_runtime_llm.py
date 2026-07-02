from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_provider_runtime_llm_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.schemas import ProviderResult
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.orchestrator import run_session_turn
from app.services.webcall_ai_production.providers import provider_runtime_llm as provider_runtime_llm_module
from app.services.webcall_ai_production.providers.provider_runtime_llm import ProviderRuntimeLLMProvider
from app.services.webcall_ai_production.providers.router import get_llm_provider
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession
from app.webchat_models import WebchatEvent


class CodexDecisionAdapter(ProviderAdapter):
    name = "codex_app_server"

    def __init__(self) -> None:
        self.requests = []

    async def generate(self, db, request):
        self.requests.append(request)
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=12,
            structured_output={
                "customer_reply": "Please share your tracking number so I can check the shipment.",
                "language": "en",
                "intent": "tracking_missing_number",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": "ask_customer_for_tracking_number",
                "ticket_should_create": False,
                "internal_summary": "Customer did not provide a tracking number.",
                "risk_flags": [],
            },
            raw_payload_safe_summary={"bridge_status": 200},
        )


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.delenv("WEBCALL_AI_PROVIDER_PROFILE", raising=False)
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "provider_runtime")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_TENANT_ID", "default")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_CHANNEL_KEY", "webcall_ai")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_SCENARIO", "webcall_ai_decision")
    monkeypatch.setattr("app.services.provider_runtime.bootstrap_provider_runtime", lambda: None)
    get_webcall_ai_production_settings.cache_clear()
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_production_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _voice_session(db) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="active",
        mode="livekit_ai_agent",
        ai_language="en",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_provider_runtime_llm_auto_selects_hybrid_profile():
    settings = get_webcall_ai_production_settings()

    assert settings.provider_profile == "hybrid"
    assert settings.stt_provider == "fake"
    assert settings.llm_provider == "provider_runtime"
    assert settings.tts_provider == "fake"
    assert settings.provider_configured is True
    assert settings.public_runtime_config()["llm_provider"] == "provider_runtime"


def test_provider_router_returns_provider_runtime_llm():
    assert isinstance(get_llm_provider("provider_runtime"), ProviderRuntimeLLMProvider)


def test_provider_runtime_llm_maps_codex_contract():
    adapter = CodexDecisionAdapter()
    ProviderRegistry.register("codex_app_server", lambda db: adapter)

    result = ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert result.response_text == "Please share your tracking number so I can check the shipment."
    assert result.intent == "tracking_missing_number"
    assert result.handoff_required is False
    assert result.provider_name == "provider_runtime:codex_app_server"
    assert adapter.requests[0].scenario == "webcall_ai_decision"
    assert adapter.requests[0].output_contract == "speedaf_webchat_fast_reply_v1"
    assert adapter.requests[0].body == "where is my parcel?"


def test_provider_runtime_llm_injects_shared_runtime_context(monkeypatch):
    captured = {}

    def fake_runtime_context(db, **kwargs):
        captured.update(kwargs)
        return {
            "context_version": "nexus_webchat_runtime_context_v2",
            "metadata_filters": {"channel": kwargs["channel_key"], "audience_scope": kwargs["audience_scope"]},
            "persona_context": {"profile_key": "voice-support"},
            "knowledge_context": {
                "retrieval": "hybrid_rag_v2",
                "total_matches": 1,
                "locked_facts": [{"item_key": "global.tracking", "answer": "Ask for a tracking number."}],
                "hits": [{"item_key": "global.tracking", "title": "Tracking support"}],
            },
            "rag_trace": {"total": 1},
            "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
        }

    monkeypatch.setattr(provider_runtime_llm_module, "build_webchat_runtime_context", fake_runtime_context)

    db = SessionLocal()
    try:
        request = provider_runtime_llm_module._build_request(db, text="where is my parcel?", language="en")
    finally:
        db.close()

    assert captured["tenant_key"] == "default"
    assert captured["channel_key"] == "webcall_ai"
    assert captured["body"] == "where is my parcel?"
    assert captured["audience_scope"] == "customer"
    assert request.metadata["runtime_context_available"] is True
    assert request.metadata["persona_context"]["profile_key"] == "voice-support"
    assert request.metadata["knowledge_context"]["retrieval"] == "hybrid_rag_v2"
    assert request.metadata["knowledge_context"]["locked_facts"][0]["item_key"] == "global.tracking"
    assert request.metadata["safety_policy"]["knowledge_scope"] == "policy_sop_faq_only"
    assert request.metadata["runtime_context_trace"]["metadata_filters"]["channel"] == "webcall_ai"


def test_session_turn_persists_provider_runtime_codex_evidence(db):
    adapter = CodexDecisionAdapter()
    ProviderRegistry.register("codex_app_server", lambda db: adapter)
    session = _voice_session(db)

    turn_result = run_session_turn(
        db,
        session=session,
        audio=b"help",
        worker_id="worker-provider-runtime",
        language="en",
    )

    turn = db.query(WebchatVoiceAITurn).one()
    action = db.query(WebchatVoiceAIAction).one()
    event_types = [row.event_type for row in db.query(WebchatEvent).filter(WebchatEvent.conversation_id == session.conversation_id).all()]

    assert turn_result["handoff_required"] is False
    assert turn.provider == "provider_runtime:codex_app_server"
    assert turn.stt_provider == "fake"
    assert turn.tts_provider == "fake"
    assert turn.intent == "tracking_missing_number"
    assert action.model_action == "tracking_missing_number"
    assert action.nexus_decision == "allowed"
    assert "webcall_ai.transcript.final" in event_types
    assert "webcall_ai.response.generated" in event_types
    assert "webcall_ai.tts.ready" in event_types
