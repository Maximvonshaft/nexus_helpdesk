import sys
from pathlib import Path

import pytest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from app.settings import get_settings
from app.services.provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply
from app.services.webchat_fast_ai_service import _apply_grounding, _result_from_provider, generate_webchat_fast_reply
from app.services.webchat_fast_config import get_webchat_fast_settings


def _clear_settings() -> None:
    get_settings.cache_clear()
    get_webchat_fast_settings.cache_clear()


def _approved_shipping_sla_context(*, include_locked: bool = True) -> dict:
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
    knowledge_context = {
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
    }
    if include_locked:
        knowledge_context["locked_facts"] = [
            {
                "item_key": "fact.ch.shipping-sla",
                "title": "瑞士海运时效",
                "question": "瑞士海运时效是多少？",
                "answer": "瑞士海运时效为 15 天。",
                "answer_mode": "direct_answer",
                "source": source,
            }
        ]
    return {
        "context_version": "nexus_webchat_runtime_context_v1",
        "knowledge_context": knowledge_context,
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
async def test_dispatch_webchat_fast_reply_ai_grounded_preserves_provider_reply():
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
                "locked_facts": [
                    {
                        "item_key": "fact.ch.address",
                        "title": "Swiss address fee",
                        "question": "Swiss address change fee",
                        "answer": "The Switzerland address-change service fee is 8 CHF.",
                        "answer_mode": "direct_answer",
                        "source": {"item_key": "fact.ch.address"},
                    }
                ],
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
                    "customer_reply": "The Switzerland address-change service fee is 8 CHF.",
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
    assert res.reply_source == "codex_app_server"
    assert res.raw_provider == "codex_app_server"
    assert res.raw_payload_safe_summary["grounding_applied"] is True
    assert res.raw_payload_safe_summary["grounded_by_ai"] is True
    assert res.raw_payload_safe_summary["grounding_validation"] == "pass"
    assert res.raw_payload_safe_summary["provider_bypassed"] is False
    mock_route.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_rejects_locked_fact_numeric_contradiction():
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
                "locked_facts": [
                    {
                        "item_key": "fact.shipping.sla",
                        "title": "运输时效",
                        "question": "海运和空运多久？",
                        "answer": "海运15天，空运10天。",
                        "answer_mode": "direct_answer",
                        "source": {"item_key": "fact.shipping.sla", "title": "运输时效"},
                    }
                ],
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

    assert res.ok is False
    assert res.error_code == "locked_fact_grounding_conflict"
    assert res.reply is None


@pytest.mark.asyncio
async def test_dispatch_webchat_fast_reply_rejects_non_equivalent_reply_before_customer_visibility():
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

    assert res.ok is False
    assert res.error_code == "locked_fact_grounding_conflict"
    assert res.reply is None


@pytest.mark.asyncio
async def test_provider_unavailable_with_locked_fact_does_not_return_direct_answer():
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_provider_down",
        body="瑞士海运时效是多少",
        recent_context=[],
        request_id="req-provider-down",
        metadata=_approved_shipping_sla_context(),
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        async def mock_route_fn(_pr_req):
            from app.services.provider_runtime.schemas import ProviderResult

            return ProviderResult(
                ok=False,
                provider="router",
                elapsed_ms=15,
                error_code="all_providers_failed",
                structured_output=None,
                raw_payload_safe_summary={"safe": True},
            )

        mock_route.side_effect = mock_route_fn
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    assert res.ok is False
    assert res.error_code == "all_providers_failed"
    assert res.reply is None
    mock_route.assert_called_once()


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
        metadata=_approved_shipping_sla_context(include_locked=False),
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
        metadata=_approved_shipping_sla_context(include_locked=False),
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
    assert res.raw_payload_safe_summary["grounding_validation"] == "not_applicable"


@pytest.mark.asyncio
async def test_deterministic_direct_answer_mode_keeps_legacy_pre_provider_escape_hatch(monkeypatch):
    monkeypatch.setenv("WEBCHAT_KNOWLEDGE_REPLY_MODE", "deterministic_direct_answer")
    _clear_settings()
    req = FastAIProviderRequest(
        tenant_key="tenant_1",
        channel_key="webchat",
        session_id="session_legacy_direct",
        body="瑞士海运时效是多少",
        recent_context=[],
        request_id="req-legacy-direct",
        metadata=_approved_shipping_sla_context(),
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route") as mock_route:
        with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"):
            res = await dispatch_webchat_fast_reply(request=req)

    _clear_settings()

    assert res.ok
    assert res.reply == "瑞士海运时效为 15 天。"
    assert res.raw_provider == "knowledge_direct_answer"
    assert res.raw_payload_safe_summary["provider_bypassed"] is True
    mock_route.assert_not_called()


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


@pytest.mark.asyncio
async def test_webchat_fast_retries_codex_direct_once_for_raw_tracking_privacy_policy_block(monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    _clear_settings()
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
        assert "raw_tracking_exposed" in request.metadata["reply_repair"]["violation_codes"]
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

    monkeypatch.setattr("app.services.webchat_fast_ai_service._runtime_context_for_request", lambda **_kwargs: runtime_context)
    monkeypatch.setattr("app.services.webchat_fast_ai_service.dispatch_webchat_fast_reply", fake_dispatch)

    result = await generate_webchat_fast_reply(
        tenant_key="default",
        channel_key="website",
        session_id="session-privacy-repair",
        body="CH1200000011425",
        recent_context=[],
        request_id="req-privacy-repair",
        tracking_fact_summary=None,
        tracking_fact_metadata={
            "fact_evidence_present": False,
            "tool_status": "failed",
            "tracking_fact_failure_reason": "1140003",
            "tracking_number_hash": "sha256:test",
        },
        tracking_fact_evidence_present=False,
    )

    _clear_settings()

    assert len(calls) == 2
    assert result.ok is True
    assert result.reply_source == "codex_direct:repaired"
    assert result.intent == "tracking_unresolved"
    assert result.ai_decision_trace["repair_applied"] is True
    assert result.ai_decision_trace["policy_gate"]["ok"] is True
    assert "CH1200000011425" not in result.reply
    assert "1200000011425" not in result.reply
    assert "server_safe_fallback" != result.reply_source
    forbidden = ("delivered", "in transit", "out for delivery", "customs", "returned", "签收", "运输中", "派送中", "清关", "退回")
    assert not any(term in result.reply.lower() for term in forbidden)
