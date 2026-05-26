import pytest
from unittest.mock import patch

from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply


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
