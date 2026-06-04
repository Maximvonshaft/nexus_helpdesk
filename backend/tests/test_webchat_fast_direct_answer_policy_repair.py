from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.provider_runtime.schemas import ProviderResult
from app.services.provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply


def _approved_shipping_sla_context() -> dict:
    source = {
        "item_key": "fact.ch.shipping-sla.repair",
        "title": "瑞士海运时效",
        "score": 161.04,
        "chunk_index": 0,
        "answer_mode": "direct_answer",
        "retrieval_method": "structured_fact_recall+direct_answer_fact",
        "source_metadata": {
            "item_key": "fact.ch.shipping-sla.repair",
            "title": "瑞士海运时效",
            "knowledge_kind": "business_fact",
            "fact_status": "approved",
            "answer_mode": "direct_answer",
            "citation": {"source": "pytest"},
        },
    }
    knowledge_context = {
        "retrieval": "hybrid_rag_v2",
        "grounding_would_apply": True,
        "grounding_source": source,
        "locked_facts": [
            {
                "item_key": "fact.ch.shipping-sla.repair",
                "title": "瑞士海运时效",
                "question": "瑞士海运时效是多少？",
                "answer": "瑞士海运时效为 15 天。",
                "answer_mode": "direct_answer",
                "source": source,
            }
        ],
        "hits": [
            {
                "item_key": "fact.ch.shipping-sla.repair",
                "title": "瑞士海运时效",
                "text": "Question: 瑞士海运时效是多少？\nAnswer: 瑞士海运时效为 15 天。",
                "score": 161.04,
                "chunk_index": 0,
                "retrieval_method": "structured_fact_recall+direct_answer_fact",
                "direct_answer": "瑞士海运时效为 15 天。",
                "answer_mode": "direct_answer",
                "metadata": {
                    "knowledge_kind": "business_fact",
                    "fact_status": "approved",
                    "answer_mode": "direct_answer",
                    "citation": {"source": "pytest"},
                },
                "source_metadata": source["source_metadata"],
            }
        ],
        "query_analysis": {
            "language": "zh",
            "entity_terms": ["瑞士"],
            "high_value_terms": ["瑞士", "海运", "时效"],
            "terms": ["瑞士", "海运", "时效"],
        },
        "candidate_count": 1,
        "total_matches": 1,
    }
    return {
        "context_version": "nexus_webchat_runtime_context_v2",
        "tenant_key": "default",
        "knowledge_context": knowledge_context,
        "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
    }


def _request(body: str = "瑞士海运时效是多少？") -> FastAIProviderRequest:
    return FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="session-direct-answer-repair",
        body=body,
        recent_context=[],
        request_id="req-direct-answer-repair",
        tracking_fact_evidence_present=False,
        metadata=_approved_shipping_sla_context(),
    )


@pytest.mark.asyncio
async def test_provider_runtime_wrong_handoff_decision_is_repaired_to_trusted_direct_answer():
    async def route_wrong_handoff(_provider_request):
        return ProviderResult(
            ok=True,
            provider="codex_app_server",
            elapsed_ms=120,
            structured_output={
                "customer_reply": "A human teammate can review this request.",
                "language": "zh",
                "intent": "handoff_request",
                "handoff_required": True,
                "handoff_reason": "ai_decision_policy_blocked",
                "ticket_should_create": True,
                "tool_calls": [{"tool_name": "handoff.request.create", "arguments": {"reason": "ai_decision_policy_blocked"}}],
                "next_action": "request_handoff",
            },
            raw_payload_safe_summary={"safe": True},
        )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route", side_effect=route_wrong_handoff):
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            result = await dispatch_webchat_fast_reply(request=_request())

    assert result.ok is True
    assert result.ai_generated is True
    assert result.reply_source == "codex_app_server"
    assert result.reply == "瑞士海运时效为 15 天。"
    assert result.handoff_required is False
    assert result.handoff_reason is None
    assert result.raw_payload_safe_summary["repair_applied"] is True
    assert result.raw_payload_safe_summary["ai_decision"]["tool_calls"] == []


@pytest.mark.asyncio
async def test_provider_runtime_safe_fallback_without_structured_output_is_not_repaired_to_direct_answer():
    async def route_unavailable(_provider_request):
        return ProviderResult.unavailable("router", "all_providers_failed", 7)

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route", side_effect=route_unavailable):
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            result = await dispatch_webchat_fast_reply(request=_request())

    assert result.ok is False
    assert result.ai_generated is False
    assert result.reply is None
    assert result.raw_provider == "provider_runtime"
    assert result.error_code == "all_providers_failed"


@pytest.mark.asyncio
async def test_explicit_human_request_is_not_repaired_by_direct_answer_policy():
    async def route_wrong_handoff(_provider_request):
        return ProviderResult(
            ok=True,
            provider="codex_app_server",
            elapsed_ms=120,
            structured_output={
                "customer_reply": "A human teammate can review this request.",
                "language": "zh",
                "intent": "handoff_request",
                "handoff_required": True,
                "handoff_reason": "customer_requested_human_review",
                "ticket_should_create": True,
            },
            raw_payload_safe_summary={"safe": True},
        )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route", side_effect=route_wrong_handoff):
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            result = await dispatch_webchat_fast_reply(request=_request("我要人工客服，瑞士海运时效是多少？"))

    assert result.ok is True
    assert result.reply == "A human teammate can review this request."
    assert result.handoff_required is True
    assert not result.raw_payload_safe_summary.get("repair_applied")
