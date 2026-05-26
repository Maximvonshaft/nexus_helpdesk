from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply
from app.services.provider_runtime.schemas import ProviderResult


def _shipping_sla_context(*, entity_terms: list[str] | None = None) -> dict:
    source = {
        "item_key": "fact.ch.shipping-sla",
        "title": "瑞士海运时效",
        "score": 161.04,
        "chunk_index": 0,
        "answer_mode": "direct_answer",
        "retrieval_method": "structured_fact_recall+direct_answer_fact",
        "source_metadata": {
            "item_key": "fact.ch.shipping-sla",
            "title": "瑞士海运时效",
            "knowledge_kind": "business_fact",
            "fact_status": "approved",
            "answer_mode": "direct_answer",
        },
    }
    return {
        "context_version": "nexus_webchat_runtime_context_v1",
        "knowledge_context": {
            "grounding_would_apply": True,
            "grounding_source": source,
            "hits": [
                {
                    "item_key": "fact.ch.shipping-sla",
                    "title": "瑞士海运时效",
                    "text": "Question: 瑞士海运时效是多少？ Answer: 瑞士海运时效为 15 天。",
                    "score": 161.04,
                    "chunk_index": 0,
                    "retrieval_method": "structured_fact_recall+direct_answer_fact",
                    "direct_answer": "瑞士海运时效为 15 天。",
                    "answer_mode": "direct_answer",
                    "metadata": {
                        "knowledge_kind": "business_fact",
                        "fact_status": "approved",
                        "answer_mode": "direct_answer",
                    },
                    "source_metadata": source["source_metadata"],
                }
            ],
            "query_analysis": {"language": "zh", "entity_terms": entity_terms or []},
            "candidate_count": 1,
            "total_matches": 1,
        },
        "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
    }


def _request(*, body: str, metadata: dict, tracking_fact_evidence_present: bool = False) -> FastAIProviderRequest:
    return FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_pr258",
        body=body,
        recent_context=[],
        request_id="req-pr258",
        metadata=metadata,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
        tracking_fact_summary="SPX123456789CH is in transit." if tracking_fact_evidence_present else None,
    )


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_returns_direct_answer_before_provider_call():
    req = _request(body="瑞士海运时效是多少", metadata=_shipping_sla_context(entity_terms=["瑞士"]))

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    mock_route.assert_not_called()
    assert res.ok is True
    assert res.ai_generated is True
    assert res.reply == "瑞士海运时效为 15 天。"
    assert res.reply_source == "knowledge_direct_answer:grounded_knowledge"
    assert res.raw_provider == "knowledge_direct_answer"
    assert res.intent == "other"
    assert res.tracking_number is None
    assert res.handoff_required is False
    assert res.raw_payload_safe_summary["grounding_applied"] is True
    assert res.raw_payload_safe_summary["grounding_reason"] == "approved_direct_answer_override"
    assert res.raw_payload_safe_summary["provider_bypassed"] is True
    assert res.raw_payload_safe_summary["provider_bypass_reason"] == "approved_direct_answer_pre_provider"
    assert res.raw_payload_safe_summary["grounding_source"]["item_key"] == "fact.ch.shipping-sla"


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_does_not_use_cross_entity_direct_answer_when_provider_unavailable():
    req = _request(body="尼日利亚海运时效是多少", metadata=_shipping_sla_context(entity_terms=["尼日利亚"]))

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_provider_request):
            return ProviderResult.unavailable(provider="codex_app_server", error_code="all_providers_failed", elapsed_ms=7)

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    mock_route.assert_called_once()
    assert res.ok is False
    assert res.error_code == "all_providers_failed"
    assert res.reply is None
    assert res.raw_payload_safe_summary.get("grounding_applied") is None


@pytest.mark.parametrize(
    "query",
    [
        "我的包裹在哪里",
        "SPX123456789CH 到哪里了",
        "这个单签收了吗",
        "我要赔偿，瑞士海运时效是多少",
        "我要投诉，瑞士海运时效是多少",
        "账号风险怎么处理，瑞士海运时效是多少",
        "司机电话是多少，瑞士海运时效是多少",
        "内部 API 怎么查瑞士海运时效",
    ],
)
@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_does_not_pre_provider_override_tracking_or_high_risk(query: str):
    req = _request(body=query, metadata=_shipping_sla_context(entity_terms=["瑞士"]))

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_provider_request):
            return ProviderResult.unavailable(provider="codex_app_server", error_code="all_providers_failed", elapsed_ms=7)

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    mock_route.assert_called_once()
    assert res.ok is False
    assert res.error_code == "all_providers_failed"
    assert res.reply_source is None


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_preserves_trusted_tracking_guard_before_provider():
    req = _request(
        body="瑞士海运时效是多少",
        metadata=_shipping_sla_context(entity_terms=["瑞士"]),
        tracking_fact_evidence_present=True,
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_provider_request):
            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=12,
                structured_output={
                    "customer_reply": "你的包裹正在运输中。",
                    "language": "zh",
                    "intent": "tracking",
                    "tracking_number": "SPX123456789CH",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    mock_route.assert_called_once()
    assert res.ok is True
    assert res.reply == "你的包裹正在运输中。"
    assert res.intent == "tracking"
    assert res.tracking_number == "SPX123456789CH"
    assert res.reply_source == "codex_app_server"
    assert res.raw_payload_safe_summary["grounding_applied"] is False
    assert res.raw_payload_safe_summary["grounding_reason"] == "trusted_tracking_output_conflict"
