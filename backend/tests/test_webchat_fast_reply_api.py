from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "true")

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from app import models_control_plane  # noqa: F401
from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import Customer, Ticket, User, WebchatRateLimitBucket
from app.models_control_plane import KnowledgeChunk, KnowledgeItem, KnowledgeItemVersion
from app.schemas_control_plane import KnowledgeItemCreate
from app.services import knowledge_service
from app.services.ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from app.services.tracking_fact_schema import TrackingFactResult
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.settings import get_settings
from app.webchat_models import WebchatConversation, WebchatHandoffRequest, WebchatMessage

BACKEND_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def setup_function():
    db = SessionLocal()
    try:
        db.execute(text("DROP TABLE IF EXISTS webchat_rate_limits"))
        db.execute(text("DROP TABLE IF EXISTS knowledge_chunks"))
        db.execute(text("DROP TABLE IF EXISTS knowledge_item_versions"))
        db.execute(text("DROP TABLE IF EXISTS knowledge_items"))
        db.commit()
    finally:
        db.close()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.execute(delete(WebchatHandoffRequest))
        db.execute(delete(WebchatMessage))
        db.execute(delete(WebchatConversation))
        db.execute(delete(Ticket))
        db.execute(delete(Customer))
        db.commit()
    finally:
        db.close()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(
    client_message_id: str = "client-1",
    *,
    session_id: str = "session-1",
    channel_key: str = "website",
    body: str = "Hi",
    recent_context: list[dict] | None = None,
) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": channel_key,
        "session_id": session_id,
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": recent_context or [],
    }


def _ai_reply(
    text: str,
    *,
    intent: str = "general_support",
    handoff: bool = False,
    handoff_reason: str | None = None,
    tracking: str | None = None,
    reply_source: str = "openclaw_responses",
) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source=reply_source,
        reply=text,
        intent=intent,
        tracking_number=tracking,
        handoff_required=handoff,
        handoff_reason=handoff_reason or ("customer_requested_human_review" if handoff else None),
        recommended_agent_action="Review the conversation and reply with verified information." if handoff else None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def _admin_user(db) -> User:
    user = db.execute(select(User).where(User.username == "api-rag-admin")).scalar_one_or_none()
    if user:
        return user
    user = User(
        username="api-rag-admin",
        display_name="API RAG Admin",
        email=f"api-rag-admin-{uuid4().hex}@example.test",
        password_hash="not-a-real-password-hash",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _seed_shipping_sla_fact(db, *, item_key: str, answer: str) -> None:
    existing = db.execute(select(KnowledgeItem).where(KnowledgeItem.item_key == item_key)).scalar_one_or_none()
    if existing:
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.item_id == existing.id))
        db.execute(delete(KnowledgeItemVersion).where(KnowledgeItemVersion.item_id == existing.id))
        db.delete(existing)
        db.flush()
    item = knowledge_service.create_item(
        db,
        KnowledgeItemCreate(
            item_key=item_key,
            title="瑞士海运时效",
            summary="瑞士海运 SLA",
            status="draft",
            source_type="text",
            knowledge_kind="business_fact",
            channel="website",
            audience_scope="customer",
            language="zh",
            priority=10,
            fact_question="瑞士海运时效是多少？",
            fact_answer=answer,
            fact_aliases_json=["瑞士海运多久", "瑞士海运时效"],
            fact_status="approved",
            answer_mode="direct_answer",
        ),
        _admin_user(db),
    )
    knowledge_service.publish_item(db, item, _admin_user(db), notes="api acceptance")


def test_low_signal_goes_to_ai_decision_without_handoff(monkeypatch):
    calls: list[dict] = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return _ai_reply("Please tell me what you need help with, such as tracking a parcel or changing delivery details.", intent="unclear")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    for idx, body in enumerate(["321", "123", "hello", "hi", "你好", "霓虹", "撒旦", "asdasd"]):
        response = client.post(
            "/api/webchat/fast-reply",
            json=_payload(f"low-signal-{idx}", session_id=f"low-signal-{idx}", body=body),
            headers={"Origin": "http://localhost"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["ai_generated"] is True
        assert payload["reply_source"] == "openclaw_responses"
        assert payload["intent"] == "unclear"
        assert payload["handoff_required"] is False
        assert payload["ai_decision_trace"]["decision"]["next_action"] in {"reply", "ask_clarifying_question"}
        assert payload["ai_decision_trace"]["policy_gate"]["ok"] is True

    assert len(calls) == 8
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 0
        assert db.execute(select(func.count(WebchatHandoffRequest.id))).scalar_one() == 0
    finally:
        db.close()


def test_explicit_human_request_is_ai_decision_tool_gated_handoff(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply(
            "I’ll ask a human teammate to review this conversation.",
            intent="handoff_request",
            handoff=True,
            handoff_reason="customer_requested_human_review",
        )

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("human-request-1", session_id="human-request-session", body="I need a human agent"),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["reply_source"] == "openclaw_responses"
    assert payload["handoff_required"] is True
    assert payload["handoff_reason"] == "customer_requested_human_review"
    assert payload["ticket_id"]
    assert payload["handoff_request_id"]
    trace = payload["ai_decision_trace"]
    assert trace["decision"]["next_action"] == "request_handoff"
    assert trace["decision"]["tool_calls"][0]["tool_name"] == "handoff.request.create"
    assert trace["tool_execution"]["records"][0]["status"] == "executed"

    replay = client.post(
        "/api/webchat/fast-reply",
        json=_payload("human-request-1", session_id="human-request-session", body="I need a human agent"),
        headers={"Origin": "http://localhost"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        assert db.execute(select(func.count(WebchatHandoffRequest.id))).scalar_one() == 1
        conversation = db.execute(select(WebchatConversation)).scalar_one()
        assert conversation.ai_suspended is True
    finally:
        db.close()


def test_refusal_request_is_ai_decision_tool_gated_handoff(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply(
            "I’ll ask a human teammate to verify whether refusal or return can be arranged for this shipment.",
            intent="refusal_request",
            handoff=True,
            handoff_reason="refusal_or_return_requires_human_review",
        )

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("refusal-request-1", session_id="refusal-request-session", body="I want to refuse delivery"),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["handoff_required"] is True
    assert payload["handoff_reason"] == "refusal_or_return_requires_human_review"
    assert payload["ai_decision_trace"]["decision"]["tool_calls"][0]["tool_name"] == "handoff.request.create"
    assert payload["ai_decision_trace"]["policy_gate"]["ok"] is True


def test_tracking_request_uses_trusted_fact_and_ai_final_reply(monkeypatch):
    calls = {"tracking": 0, "ai": 0}

    def fake_lookup_fast_tracking_fact(**kwargs):
        calls["tracking"] += 1
        assert kwargs["tracking_number"] == "CH020000006856"
        return TrackingFactResult(
            ok=True,
            tracking_number=kwargs["tracking_number"],
            status="10",
            status_label="In transit",
            checked_at="2026-06-01T00:00:00Z",
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=True,
        )

    async def fake_generate(**kwargs):
        calls["ai"] += 1
        assert kwargs["tracking_fact_evidence_present"] is True
        assert kwargs["tracking_fact_summary"]
        return _ai_reply("Your parcel ending 006856 is currently In transit.", intent="tracking", tracking="CH020000006856")

    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_lookup_fast_tracking_fact)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("tracking-ai-1", session_id="tracking-ai-session", body="Track CH020000006856"),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["reply_source"] == "openclaw_responses"
    assert payload["intent"] == "tracking"
    assert payload["tracking_number"] is None
    assert payload["tracking_number_suffix"] == "006856"
    assert payload["tracking_fact"]["fact_evidence_present"] is True
    assert payload["evidence_trace"]["source"] == "speedaf_trusted_tracking_fact"
    trace = payload["ai_decision_trace"]
    assert trace["decision"]["tool_calls"][0]["tool_name"] == "speedaf.order.query"
    assert trace["tool_execution"]["records"][0]["status"] == "already_resolved_by_context"
    assert "CH020000006856" not in json.dumps(payload, ensure_ascii=False)
    assert calls == {"tracking": 1, "ai": 1}


def test_unsupported_tracking_status_claim_is_blocked(monkeypatch):
    def fake_lookup_fast_tracking_fact(**kwargs):
        return TrackingFactResult(
            ok=False,
            tracking_number=kwargs["tracking_number"],
            tool_status="timeout",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="timeout",
        )

    async def fake_generate(**kwargs):
        return _ai_reply("Your parcel ending 006856 is delivered.", intent="tracking", tracking="CH020000006856")

    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_lookup_fast_tracking_fact)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("tracking-blocked-1", session_id="tracking-blocked-session", body="where is my parcel CH020000006856"),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reply_source"] == "server_safe_fallback"
    assert payload["handoff_required"] is True
    assert payload["ai_decision_trace"]["policy_gate"]["ok"] is False
    assert payload["ai_decision_trace"]["policy_gate"]["violations"][0]["code"] in {"tracking_status_without_trusted_fact", "unsafe_customer_reply"}
    assert "CH020000006856" not in json.dumps(payload, ensure_ascii=False)


def test_no_evidence_tracking_raw_identifier_policy_repair_avoids_server_safe_fallback(monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    get_webchat_fast_settings.cache_clear()
    calls: list[FastAIProviderRequest] = []

    runtime_context = {
        "context_version": "nexus_webchat_runtime_context_v2",
        "knowledge_context": {
            "retrieval_query": "CH1200000011425 运单号格式 wrong tracking number",
            "query_expansion_terms": ["运单号格式", "wrong tracking number"],
            "hits": [
                {
                    "item_key": "ch.waybill.format",
                    "title": "瑞士 Speedaf 运单号格式与输错提醒",
                    "text": "CH waybills should use CH followed by 12 digits.",
                    "metadata": {"knowledge_kind": "business_fact", "fact_status": "approved", "answer_mode": "guided_answer"},
                }
            ],
            "locked_facts": [],
            "evidence_pack": [{"item_key": "ch.waybill.format", "published_version": 1}],
            "total_matches": 1,
            "candidate_count": 1,
        },
    }

    def fake_lookup_fast_tracking_fact(**kwargs):
        assert kwargs["tracking_number"] == "CH1200000011425"
        return TrackingFactResult(
            ok=False,
            tracking_number=kwargs["tracking_number"],
            tool_status="failed",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="1140003",
        )

    async def fake_dispatch(*, request: FastAIProviderRequest):
        calls.append(request)
        if len(calls) == 1:
            return FastAIProviderResult(
                ok=True,
                ai_generated=True,
                reply_source="codex_direct",
                raw_provider="codex_direct",
                raw_payload_safe_summary={"provider": "codex_direct"},
                reply="I could not find a trusted live record for CH1200000011425. Please verify CH1200000011425 follows the CH + 12 digit format.",
                intent="tracking_unresolved",
                tracking_number=None,
                handoff_required=False,
                handoff_reason=None,
                recommended_agent_action=None,
                elapsed_ms=4304,
            )
        assert request.metadata["reply_repair"]["mode"] == "customer_reply_privacy_repair"
        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="codex_direct",
            raw_provider="codex_direct",
            raw_payload_safe_summary={"provider": "codex_direct"},
            reply="I could not find a trusted live record for the waybill number you provided. Please verify it follows the CH + 12 digit format and resend it if needed.",
            intent="tracking_unresolved",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=5100,
        )

    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_lookup_fast_tracking_fact)
    monkeypatch.setattr(webchat_fast, "_webchat_fast_runtime_context", lambda **_kwargs: runtime_context)
    monkeypatch.setattr("app.services.webchat_fast_ai_service._runtime_context_for_request", lambda **_kwargs: runtime_context)
    monkeypatch.setattr("app.services.webchat_fast_ai_service.dispatch_webchat_fast_reply", fake_dispatch)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("tracking-no-evidence-repair", session_id="tracking-no-evidence-repair-session", body="CH1200000011425"),
        headers={"Origin": "http://localhost"},
    )

    get_webchat_fast_settings.cache_clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(calls) == 2
    assert payload["ok"] is True
    assert payload["reply_source"] == "codex_direct:repaired"
    assert payload["reply_source"] != "server_safe_fallback"
    assert payload["intent"] == "tracking_unresolved"
    assert payload["tracking_fact"]["fact_evidence_present"] is False
    assert payload["ai_decision_trace"]["policy_gate"]["ok"] is True
    assert payload["ai_decision_trace"]["repair_applied"] is True
    assert "CH1200000011425" not in json.dumps(payload, ensure_ascii=False)
    assert "1200000011425" not in payload["reply"]
    forbidden = ("delivered", "in transit", "out for delivery", "customs", "returned", "签收", "运输中", "派送中", "清关", "退回")
    assert not any(term in payload["reply"].lower() for term in forbidden)


def test_provider_failure_falls_back_safely_without_500(monkeypatch):
    async def fake_generate(**_kwargs):
        return WebchatFastReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=None,
            reply=None,
            intent=None,
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=42,
            error_code="all_providers_failed",
        )

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("provider-failure-safe-fallback", session_id="provider-failure-safe-session", body="Tell me about your services"),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ai_generated"] is False
    assert payload["reply_source"] == "server_safe_fallback"
    assert payload["evidence_trace"]["source"] == "server_safe_fallback"
    assert payload["ai_decision_trace"]["mode"] in {"emergency_fallback_only", "gated"}


def test_fast_reply_same_session_reuses_conversation_and_ai_context(monkeypatch):
    seen_contexts: list[list[dict]] = []

    async def fake_generate(**kwargs):
        seen_contexts.append(kwargs["recent_context"])
        if kwargs["body"] == "Where is my parcel?":
            return _ai_reply("Please provide your tracking number.", intent="tracking_missing_number")
        return _ai_reply("I received the tracking number and will check it with trusted shipment data.", intent="tracking", tracking="SPX123456789CH")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply", json=_payload("msg-0001", body="Where is my parcel?"))
    second = client.post("/api/webchat/fast-reply", json=_payload("msg-0002", body="SPX123456789CH"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(seen_contexts) == 2
    assert second.json()["tracking_number"] is None
    assert second.json()["tracking_number_suffix"] == "6789CH"
    assert "SPX123456789CH" not in json.dumps(second.json(), ensure_ascii=False)

    db = SessionLocal()
    try:
        conversations = db.execute(select(WebchatConversation).where(WebchatConversation.fast_session_id == "session-1")).scalars().all()
        assert len(conversations) == 1
        messages = db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversations[0].id)).scalars().all()
        assert [m.direction for m in messages].count("visitor") == 2
        assert [m.direction for m in messages].count("ai") == 2
        ai_metadata = [json.loads(m.metadata_json or "{}") for m in messages if m.direction == "ai"]
        assert all("ai_decision_trace" in item for item in ai_metadata)
    finally:
        db.close()


def test_fast_reply_published_business_sla_direct_answer(monkeypatch):
    answer = "瑞士海运时效为 15 天。"
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_KNOWLEDGE_REPLY_MODE", "deterministic_direct_answer")
    get_settings.cache_clear()
    get_webchat_fast_settings.cache_clear()
    db = SessionLocal()
    try:
        _seed_shipping_sla_fact(db, item_key="fact.ch.shipping-sla.api", answer=answer)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("provider-runtime-shipping-sla", session_id="provider-runtime-shipping-sla-session", body="瑞士海运时效是多少？"),
        headers={"Origin": "http://localhost"},
    )

    get_webchat_fast_settings.cache_clear()
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "15" in payload["reply"]
    assert payload["grounding_applied"] is True
    assert payload["ai_decision_trace"]["mode"] == "trusted_kb_direct_answer_pre_provider"


def test_fast_handoff_same_tracking_number_reuses_ticket_across_sessions(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply("A human teammate will review the lost parcel report.", intent="complaint", handoff=True, handoff_reason="lost_or_damaged_parcel_requires_human_review", tracking="SPX123456789CH")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    first = client.post("/api/webchat/fast-reply", json=_payload("msg-a001", session_id="session-a", body="My parcel is lost SPX123456789CH"))
    second = client.post("/api/webchat/fast-reply", json=_payload("msg-b001", session_id="session-b", body="My parcel is lost SPX123456789CH"))
    assert first.status_code == 200
    assert second.status_code == 200

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        assert db.execute(select(func.count(WebchatHandoffRequest.id))).scalar_one() == 1
    finally:
        db.close()


def test_fast_customer_external_ref_is_channel_scoped_for_same_session(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply("A human teammate will review this request.", intent="complaint", handoff=True, handoff_reason="complaint_requires_human_review")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    first = client.post(
        "/api/webchat/fast-reply",
        json=_payload("channel-customer-1", session_id="shared-browser-session", channel_key="website", body="Please escalate WEB111111111CH"),
    )
    second = client.post(
        "/api/webchat/fast-reply",
        json=_payload("channel-customer-2", session_id="shared-browser-session", channel_key="mobile", body="Please escalate MOB222222222CH"),
    )
    assert first.status_code == 200
    assert second.status_code == 200

    db = SessionLocal()
    try:
        customers = db.execute(select(Customer).order_by(Customer.external_ref.asc())).scalars().all()
        assert [customer.external_ref for customer in customers] == [
            "webchat-fast:default:mobile:shared-browser-session",
            "webchat-fast:default:website:shared-browser-session",
        ]
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 2
    finally:
        db.close()


def test_fast_reply_idempotency_same_client_message_id_returns_cached_response(monkeypatch):
    calls = {"generate": 0}

    async def fake_generate(**kwargs):
        calls["generate"] += 1
        return _ai_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    first = client.post("/api/webchat/fast-reply", json=_payload("same-msg"))
    second = client.post("/api/webchat/fast-reply", json=_payload("same-msg"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert calls["generate"] == 1
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatMessage.id))).scalar_one() == 2
    finally:
        db.close()


def test_fast_reply_different_session_creates_different_conversation(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    assert client.post("/api/webchat/fast-reply", json=_payload("msg-1001", session_id="session-a")).status_code == 200
    assert client.post("/api/webchat/fast-reply", json=_payload("msg-1002", session_id="session-b")).status_code == 200
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
    finally:
        db.close()


def test_demo_payload_does_not_force_empty_context():
    source = (BACKEND_ROOT / "app/static/webchat/demo/js/app.js").read_text(encoding="utf-8")
    assert "recent_context: []" not in source
    assert "sessionStorage" in source
    assert "recentContext.slice" in source


def test_widget_persists_recent_context():
    source = (BACKEND_ROOT / "app/static/webchat/widget.js").read_text(encoding="utf-8")
    assert "contextKey" in source
    assert "sessionStorage.setItem(contextKey" in source
    assert "function buildApiRecentContext()" in source
    assert "recent_context: buildApiRecentContext()" in source
    assert "recent_context: state.recentContext" not in source


def test_fast_rate_limit_still_blocks_rotated_sessions(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")

    async def fake_generate(**kwargs):
        return _ai_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    headers = {"User-Agent": "pytest-fast-limit/1.0"}
    first = client.post("/api/webchat/fast-reply", json=_payload("rl-1", session_id="session-a"), headers=headers)
    second = client.post("/api/webchat/fast-reply", json=_payload("rl-2", session_id="session-b"), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatRateLimitBucket.id))).scalar_one() == 1
    finally:
        db.close()
