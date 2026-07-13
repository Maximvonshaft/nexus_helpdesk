from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_provider_runtime_llm_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.schemas import ProviderResult
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.orchestrator import run_session_turn
from app.services.webcall_ai_production.providers.base import ProviderError
from app.services.webcall_ai_production.providers.provider_runtime_llm import ProviderRuntimeLLMProvider
from app.services.webcall_ai_production.providers.router import get_llm_provider
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession
from app.webchat_models import WebchatEvent


class RuntimeDecisionAdapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self) -> None:
        self.requests = []

    async def generate(self, db, request):
        self.requests.append(request)
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=12,
            structured_output={
                "customer_reply": "Could you send the shipment reference so I can check the shipment.",
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


class RuntimeFailureAdapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self) -> None:
        self.requests = []

    async def generate(self, db, request):
        self.requests.append(request)
        return ProviderResult.unavailable(
            self.name,
            "synthetic_shadow_failure",
            12,
            fallback_allowed=False,
        )


class RuntimeParseRejectAdapter(RuntimeDecisionAdapter):
    marker = "secret.invalid.intent.marker"

    async def generate(self, db, request):
        result = await super().generate(db, request)
        result.structured_output["intent"] = self.marker
        return result


def _drop_provider_runtime_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS provider_runtime_audit_logs"))
        conn.execute(text("DROP TABLE IF EXISTS provider_routing_rules"))


def _create_provider_runtime_tables() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE provider_routing_rules (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(36) NOT NULL,
                    channel_key VARCHAR(100) NOT NULL,
                    scenario VARCHAR(100) NOT NULL,
                    primary_provider VARCHAR(100) NOT NULL,
                    fallback_providers JSON,
                    output_contract VARCHAR(100) NOT NULL,
                    timeout_ms INTEGER NOT NULL,
                    canary_percent INTEGER NOT NULL DEFAULT 0,
                    kill_switch BOOLEAN NOT NULL DEFAULT 0,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE provider_runtime_audit_logs (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(36) NOT NULL,
                    provider VARCHAR(100) NOT NULL,
                    request_id VARCHAR(100) NOT NULL,
                    channel_key VARCHAR(100) NOT NULL,
                    session_id VARCHAR(100),
                    operation VARCHAR(50) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    safe_summary JSON,
                    error_code VARCHAR(255),
                    elapsed_ms INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    _drop_provider_runtime_tables()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _create_provider_runtime_tables()
    monkeypatch.setenv("WEBCALL_AI_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    monkeypatch.delenv("WEBCALL_AI_PROVIDER_PROFILE", raising=False)
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "provider_runtime")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER", "private_ai_runtime")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_TENANT_ID", "default")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_CHANNEL_KEY", "webcall_ai")
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_SCENARIO", "webcall_ai_decision")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "100")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setattr("app.services.provider_runtime.bootstrap_provider_runtime", lambda: None)
    get_webcall_ai_production_settings.cache_clear()
    yield
    _drop_provider_runtime_tables()
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


def _provider_audits(db) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT operation, status, safe_summary, error_code
            FROM provider_runtime_audit_logs
            ORDER BY created_at ASC
            """
        )
    ).mappings().all()
    output = []
    for row in rows:
        summary = row["safe_summary"]
        if isinstance(summary, str):
            summary = json.loads(summary)
        output.append({**dict(row), "safe_summary": summary or {}})
    return output


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


def test_private_runtime_alias_routes_through_authoritative_router(db):
    adapter = RuntimeDecisionAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    result = ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert result.response_text == "Could you send the shipment reference so I can check the shipment."
    assert result.intent == "tracking_missing_number"
    assert result.handoff_required is False
    assert result.provider_name == "provider_runtime:private_ai_runtime"
    assert adapter.requests[0].scenario == "webcall_ai_decision"
    assert adapter.requests[0].output_contract == "nexus_webchat_runtime_reply_v1"
    assert adapter.requests[0].body == "where is my parcel?"
    audits = _provider_audits(db)
    assert len(audits) == 1
    traffic = audits[0]["safe_summary"]["traffic_selection"]
    assert traffic["path"] == "canary_authoritative"
    assert traffic["authoritative"] is True


def test_webcall_control_mode_suppresses_direct_alias_candidate(monkeypatch, db):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "control")
    adapter = RuntimeDecisionAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    result = ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert result.response_text == ""
    assert result.intent == "provider_runtime_non_authoritative"
    assert result.handoff_required is False
    assert result.handoff_reason is None
    assert result.provider_name == "provider_runtime:provider_canary_control_path"
    assert adapter.requests == []
    audits = _provider_audits(db)
    assert audits[-1]["operation"] == "traffic_select"
    assert audits[-1]["safe_summary"]["traffic_selection"]["path"] == "control"


def test_webcall_shadow_failure_is_neutral_and_non_authoritative(monkeypatch, db):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    adapter = RuntimeFailureAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    result = ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert result.response_text == ""
    assert result.intent == "provider_runtime_non_authoritative"
    assert result.handoff_required is False
    assert result.handoff_reason is None
    assert result.provider_name == "provider_runtime:provider_shadow_failed"
    assert len(adapter.requests) == 1
    audits = _provider_audits(db)
    assert audits[-1]["error_code"] == "provider_shadow_failed"
    assert audits[-1]["safe_summary"]["traffic_selection"]["path"] == "shadow_only"
    assert audits[-1]["safe_summary"]["traffic_selection"]["authoritative"] is False


def test_parse_reject_audit_uses_fixed_code_without_provider_marker(db):
    adapter = RuntimeParseRejectAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    with pytest.raises(ProviderError) as caught:
        ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert caught.value.code == "all_providers_failed"
    audits = _provider_audits(db)
    parse_audit = next(item for item in audits if item["operation"] == "parse_reject")
    summary = parse_audit["safe_summary"]
    assert parse_audit["error_code"] == "parse_reject"
    assert "parse_error" not in summary
    assert summary["parse_error_code"] == "output_contract_rejected"
    assert RuntimeParseRejectAdapter.marker not in json.dumps(summary)


def test_webcall_kill_switch_suppresses_alias_even_with_invalid_lower_settings(monkeypatch, db):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "invalid")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "invalid")
    adapter = RuntimeDecisionAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    with pytest.raises(ProviderError) as caught:
        ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert caught.value.code == "kill_switch_active"
    assert adapter.requests == []
    traffic = _provider_audits(db)[-1]["safe_summary"]["traffic_selection"]
    assert traffic["path"] == "kill_switch"
    assert traffic["execute_candidate"] is False


def test_webcall_rejects_unapproved_provider_alias_without_adapter_call(monkeypatch, db):
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER", "unapproved")
    adapter = RuntimeDecisionAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)

    with pytest.raises(ProviderError) as caught:
        ProviderRuntimeLLMProvider().respond("where is my parcel?", language="en")

    assert caught.value.code == "provider_runtime_provider_not_allowed"
    assert adapter.requests == []
    assert _provider_audits(db) == []


def test_session_turn_persists_provider_runtime_result_and_governed_evidence(db):
    adapter = RuntimeDecisionAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda session: adapter)
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
    event_types = [
        row.event_type
        for row in db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == session.conversation_id)
        .all()
    ]

    assert turn_result["handoff_required"] is False
    assert turn.provider == "provider_runtime:private_ai_runtime"
    assert turn.stt_provider == "fake"
    assert turn.tts_provider == "fake"
    assert turn.intent == "tracking_missing_number"
    assert action.model_action == "tracking_missing_number"
    assert action.nexus_decision == "allowed"
    assert "webcall_ai.transcript.final" in event_types
    assert "webcall_ai.response.generated" in event_types
