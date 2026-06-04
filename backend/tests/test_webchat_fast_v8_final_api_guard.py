from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_v8_final_api_guard_tests.db")
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
from app.services.tracking_fact_schema import TrackingFactResult
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.webchat_models import WebchatConversation, WebchatHandoffRequest, WebchatMessage

client = TestClient(app)

ANSWER = "KB_EVIDENCE_CHAIN_20260604_074247_3691793_CHF_17_40"


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
        db.execute(delete(WebchatFastRateLimitBucket) if False else delete(WebchatRateLimitBucket))
        db.execute(delete(WebchatHandoffRequest))
        db.execute(delete(WebchatMessage))
        db.execute(delete(WebchatConversation))
        db.execute(delete(Ticket))
        db.execute(delete(Customer))
        db.commit()
    finally:
        db.close()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(client_message_id: str, *, session_id: str, body: str) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": session_id,
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": [],
    }


def _trusted_runtime_context() -> dict:
    return {
        "knowledge_context": {
            "retrieval": "hybrid_rag_v2",
            "grounding_would_apply": True,
            "total_matches": 1,
            "candidate_count": 1,
            "grounding_source": {
                "item_key": "audit.kb.evidence.20260604-074247-3691793",
                "title": "KB evidence chain final guard probe",
                "source_metadata": {"controlled_fact": True, "source": "v8_final_api_guard_test"},
            },
            "hits": [
                {
                    "item_key": "audit.kb.evidence.20260604-074247-3691793",
                    "title": "KB evidence chain final guard probe",
                    "score": 99.0,
                    "direct_answer": ANSWER,
                    "answer_mode": "direct_answer",
                    "metadata": {
                        "knowledge_kind": "business_fact",
                        "fact_status": "approved",
                        "answer_mode": "direct_answer",
                        "citation": {"source": "v8_final_api_guard_test"},
                    },
                    "source_metadata": {"controlled_fact": True, "source": "v8_final_api_guard_test"},
                    "retrieval_method": "hybrid_rag_v2",
                }
            ],
        }
    }


def _ai_handoff_reply() -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="openclaw_responses",
        reply="I’ll ask a human teammate to review this conversation.",
        intent="handoff_request",
        tracking_number=None,
        handoff_required=True,
        handoff_reason="customer_requested_human_review",
        recommended_agent_action="Review the conversation and reply with verified information.",
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def _provider_failure_result() -> WebchatFastReplyResult:
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


def test_v8_final_api_guard_returns_trusted_direct_answer_without_handoff(monkeypatch):
    async def fake_generate(**_kwargs):
        return _provider_failure_result()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "_webchat_fast_runtime_context", lambda **_kwargs: _trusted_runtime_context())

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "v8-final-guard-1",
            session_id=f"v8-final-guard-{uuid4().hex}",
            body=f"Return the exact phrase: {ANSWER}",
        ),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ai_generated"] is True
    assert payload["reply"] == ANSWER
    assert payload["reply_source"] == "provider_runtime:trusted_kb_direct_answer"
    assert payload["handoff_required"] is False
    assert payload["handoff_reason"] is None
    assert payload.get("ticket_id") is None
    assert payload.get("handoff_request_id") is None
    assert payload["grounding_applied"] is True
    assert payload["grounding_reason"] == "trusted_kb_direct_answer_final_api_guard_v8"
    assert payload["evidence_trace"]["retrieval"] == "hybrid_rag_v2"
    assert payload["evidence_trace"]["final_api_guard_applied"] is True
    assert payload["ai_decision_trace"]["final_api_guard_applied"] is True
    assert payload["ai_decision_trace"]["decision"]["tool_calls"] == []
    assert payload["ai_decision_trace"]["tool_execution"]["records"] == []

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 0
        assert db.execute(select(func.count(WebchatHandoffRequest.id))).scalar_one() == 0
    finally:
        db.close()


def test_v8_final_api_guard_not_applied_for_explicit_human_request(monkeypatch):
    async def fake_generate(**_kwargs):
        return _ai_handoff_reply()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "_webchat_fast_runtime_context", lambda **_kwargs: _trusted_runtime_context())

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "v8-human-1",
            session_id=f"v8-human-{uuid4().hex}",
            body="I need a human agent",
        ),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reply_source"] == "openclaw_responses"
    assert payload["handoff_required"] is True
    assert payload["handoff_reason"] == "customer_requested_human_review"
    assert payload["ticket_id"]
    assert payload["handoff_request_id"]
    assert payload["ai_decision_trace"]["decision"]["tool_calls"][0]["tool_name"] == "handoff.request.create"
    assert payload["ai_decision_trace"]["tool_execution"]["records"][0]["status"] == "executed"

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        assert db.execute(select(func.count(WebchatHandoffRequest.id))).scalar_one() == 1
    finally:
        db.close()


def test_v8_final_api_guard_not_applied_for_tracking_fact(monkeypatch):
    def fake_lookup_fast_tracking_fact(**kwargs):
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

    async def fake_generate(**_kwargs):
        return WebchatFastReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            reply="Your parcel ending 006856 is currently In transit.",
            intent="tracking",
            tracking_number="CH020000006856",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=20,
        )

    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_lookup_fast_tracking_fact)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "_webchat_fast_runtime_context", lambda **_kwargs: _trusted_runtime_context())

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "v8-tracking-1",
            session_id=f"v8-tracking-{uuid4().hex}",
            body="Track CH020000006856",
        ),
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reply_source"] == "openclaw_responses"
    assert payload["intent"] == "tracking"
    assert payload["tracking_fact"]["fact_evidence_present"] is True
    assert payload["evidence_trace"]["source"] == "speedaf_trusted_tracking_fact"
    assert payload["tracking_number"] is None
    assert payload["tracking_number_suffix"] == "006856"
