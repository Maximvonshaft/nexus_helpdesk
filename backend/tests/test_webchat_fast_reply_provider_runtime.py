import sys
from pathlib import Path

import pytest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from app.services.provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply
from app.services.webchat_fast_ai_service import _apply_grounding, _result_from_provider


def _approved_shipping_sla_context() -> dict:
    source = {
        "item_key": "fact.ch.shipping-sla",
        "title": "瑞士海运时效",
        "score": 161.04,
        "chunk_index": 0,
        "answer_mode": "direct_answer",
        "retrieval_method": "structured_fact_recall+direct_answer_fact",
        "source_metadata": {
            "item_key": "fact.ch.shipping-sla",
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
            "query_analysis": {"language": "zh", "high_value_terms": ["瑞士", "海运", "时效"], "terms": ["瑞士", "海运", "时效"]},
            "candidate_count": 1,
            "total_matches": 1,
        },
        "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
    }


def _tracking_missing_number_output() -> dict:
    return {
        "customer_reply": "请提供您的运单号，我才能查询包裹状态。",
        "language": "zh",
        "intent": "tracking_missing_number",
        "tracking_number": None,
        "handoff_required": False,
        "ticket_should_create": False,
    }


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_with_provider_runtime():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_1",
        body="Where is my package?",
        recent_context=[],
        request_id="req1",
        tracking_fact_summary="In transit",
        tracking_fact_metadata={"number": "123"},
        tracking_fact_evidence_present=True,
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=150,
                structured_output={
                    "customer_reply": "It's on the way.",
                    "intent": "tracking",
                    "tracking_number": "123",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn

        with patch("app.services.provider_runtime.webchat_fast_dispatcher.build_webchat_runtime_context") as mock_context:
            mock_context.return_value = {
                "persona_context": {"profile_key": "default.website.en"},
                "knowledge_context": {"hits": [{"item_key": "faq", "text": "Address changes before dispatch."}]},
                "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
            }
            with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
                res = await dispatch_webchat_fast_reply(request=req)

                assert res.ok
                assert res.ai_generated
                assert res.reply == "It's on the way."
                assert res.intent == "tracking"
                assert res.tracking_number == "123"
                assert res.reply_source == "codex_app_server"
                mock_route.assert_called_once()
                provider_request = mock_route.call_args.args[0]
                assert provider_request.metadata["persona_context"]["profile_key"] == "default.website.en"
                assert provider_request.metadata["knowledge_context"]["hits"][0]["item_key"] == "faq"
                assert provider_request.metadata["tracking_fact_metadata"] == {"number": "123"}


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_applies_direct_answer_grounding():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_qa",
        body="Swiss address change fee",
        recent_context=[],
        request_id="req-grounding",
        metadata={
            "context_version": "nexus_webchat_runtime_context_v1",
            "knowledge_context": {
                "hits": [
                    {
                        "item_key": "fact.ch.address",
                        "title": "Swiss address fee",
                        "score": 42.0,
                        "chunk_index": 0,
                        "retrieval_method": "structured_fact_recall+direct_answer_fact",
                        "direct_answer": "The Switzerland address-change service fee is 8 CHF.",
                        "answer_mode": "direct_answer",
                        "metadata": {"knowledge_kind": "business_fact", "fact_status": "approved", "answer_mode": "direct_answer"},
                        "source_metadata": {"item_key": "fact.ch.address"},
                    }
                ],
                "query_analysis": {"language": "en", "high_value_terms": ["swiss", "address"], "terms": ["swiss", "address"]},
                "candidate_count": 1,
                "total_matches": 1,
            },
            "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
        },
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=120,
                structured_output={
                    "customer_reply": "I cannot confirm that from the available information.",
                    "language": "en",
                    "intent": "other",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    assert res.ok
    assert res.reply == "The Switzerland address-change service fee is 8 CHF."
    assert res.reply_source == "codex_app_server:grounded_knowledge"
    assert res.raw_payload_safe_summary["grounding_applied"] is True


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_applies_grounding_on_safe_numeric_contradiction():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_sla",
        body="海运和空运多久？",
        recent_context=[],
        request_id="req-grounding-conflict",
        metadata={
            "context_version": "nexus_webchat_runtime_context_v1",
            "knowledge_context": {
                "hits": [
                    {
                        "item_key": "fact.shipping.sla",
                        "title": "运输时效",
                        "score": 42.0,
                        "chunk_index": 0,
                        "retrieval_method": "structured_fact_recall+direct_answer_fact",
                        "direct_answer": "海运15天，空运10天。",
                        "answer_mode": "direct_answer",
                        "metadata": {"knowledge_kind": "faq", "fact_status": "approved", "answer_mode": "direct_answer"},
                        "source_metadata": {"item_key": "fact.shipping.sla"},
                    }
                ],
                "query_analysis": {"language": "zh", "high_value_terms": ["海运", "空运"], "terms": ["海运", "空运"]},
                "candidate_count": 1,
                "total_matches": 1,
            },
            "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
        },
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=120,
                structured_output={
                    "customer_reply": "通常需要30-45天。",
                    "language": "zh",
                    "intent": "other",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    assert res.ok
    assert res.reply == "海运15天，空运10天。"
    assert res.reply_source == "codex_app_server:grounded_knowledge"
    assert res.raw_payload_safe_summary["grounding_applied"] is True


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_overrides_tracking_missing_number_with_approved_direct_answer():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_pr256",
        body="瑞士海运时效是多少",
        recent_context=[],
        request_id="req-pr256",
        metadata=_approved_shipping_sla_context(),
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=120,
                structured_output=_tracking_missing_number_output(),
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    assert res.ok
    assert res.reply == "瑞士海运时效为 15 天。"
    assert res.intent == "other"
    assert res.tracking_number is None
    assert res.reply_source == "codex_app_server:grounded_knowledge"
    assert res.raw_payload_safe_summary["grounding_applied"] is True
    assert res.raw_payload_safe_summary["grounding_reason"] == "approved_direct_answer_override"


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
async def test_dispatch_webchat_fast_reply_blocks_direct_answer_override_for_tracking_and_high_risk_queries(query):
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_pr256_negative",
        body=query,
        recent_context=[],
        request_id="req-pr256-negative",
        metadata=_approved_shipping_sla_context(),
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=120,
                structured_output=_tracking_missing_number_output(),
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    assert res.ok
    assert res.reply == "请提供您的运单号，我才能查询包裹状态。"
    assert res.intent == "tracking_missing_number"
    assert res.reply_source == "codex_app_server"
    assert res.raw_payload_safe_summary["grounding_applied"] is False


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_preserves_trusted_tracking_output_over_direct_answer():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_pr256_tracking_evidence",
        body="瑞士海运时效是多少",
        recent_context=[],
        request_id="req-pr256-tracking-evidence",
        tracking_fact_evidence_present=True,
        tracking_fact_summary="SPX123456789CH is in transit.",
        metadata=_approved_shipping_sla_context(),
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=True,
                provider="codex_app_server",
                elapsed_ms=120,
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

    assert res.ok
    assert res.reply == "你的包裹正在运输中。"
    assert res.intent == "tracking"
    assert res.tracking_number == "SPX123456789CH"
    assert res.reply_source == "codex_app_server"
    assert res.raw_payload_safe_summary["grounding_applied"] is False
    assert res.raw_payload_safe_summary["grounding_reason"] == "trusted_tracking_output_conflict"


def test_second_pass_grounding_preserves_provider_runtime_grounded_telemetry():
    source = {"item_key": "fact.ch.shipping-sla", "title": "瑞士海运时效"}
    provider_result = FastAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="codex_app_server:grounded_knowledge",
        raw_provider="codex_app_server",
        raw_payload_safe_summary={
            "grounding_applied": True,
            "grounding_source": source,
            "grounding_reason": "approved_direct_answer_override",
        },
        reply="瑞士海运时效为 15 天。",
        intent="other",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=12,
    )

    grounded = _apply_grounding(
        provider_result=provider_result,
        body="瑞士海运时效是多少",
        runtime_context={
            "context_version": "nexus_webchat_runtime_context_v1",
            "knowledge_context": {"hits": []},
        },
        tracking_fact_evidence_present=False,
    )
    result = _result_from_provider(grounded)

    assert grounded.raw_payload_safe_summary["grounding_applied"] is True
    assert grounded.raw_payload_safe_summary["grounding_reason"] == "approved_direct_answer_override"
    assert result.grounding_applied is True
    assert result.grounding_source == source
    assert result.grounding_reason == "approved_direct_answer_override"
