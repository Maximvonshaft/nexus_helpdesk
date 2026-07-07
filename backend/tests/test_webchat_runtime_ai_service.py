from __future__ import annotations

import pytest

import app.services.webchat_runtime_ai_service as webchat_runtime_ai_service
from app.services.ai_runtime.schemas import RuntimeAIProviderResult
from app.services.webchat_runtime_output_parser import RuntimeReplyParseError
from app.services.webchat_runtime_ai_service import _result_from_provider


def test_tracking_number_for_policy_ignores_plain_alphanumeric_reference_metadata():
    tracking = webchat_runtime_ai_service._tracking_number_for_policy(
        body="请告诉我生产知识闭环暗号 mr98fg0u",
        tracking_fact_metadata={"tracking_number": "mr98fg0u"},
    )

    assert tracking is None


def test_tracking_number_for_policy_keeps_real_ch_waybill_metadata():
    tracking = webchat_runtime_ai_service._tracking_number_for_policy(
        body="请帮我看看 CH020000129135",
        tracking_fact_metadata={"tracking_number": "CH020000129135"},
    )

    assert tracking == "CH020000129135"


def test_latency_profile_uses_unified_runtime_for_general_knowledge_query():
    runtime_context = {
        "knowledge_context": {
            "hits": [
                {
                    "item_key": "qa.production.answer",
                    "title": "生产知识闭环冒烟",
                    "answer_mode": "direct_answer",
                    "direct_answer": "生产知识闭环暗号是 canyon-lime。",
                    "customer_visible": True,
                }
            ],
            "locked_facts": [
                {
                    "item_key": "qa.production.answer",
                    "title": "生产知识闭环冒烟",
                    "answer_mode": "direct_answer",
                    "answer": "生产知识闭环暗号是 canyon-lime。",
                    "customer_visible": True,
                }
            ],
        }
    }

    latency_class = webchat_runtime_ai_service._latency_class_with_runtime_context(
        "standard",
        body="请告诉我生产知识闭环暗号 mr98jqnv",
        runtime_context=runtime_context,
        evidence_present=False,
    )

    assert latency_class == "unified_ai_runtime"


def test_latency_profile_uses_unified_runtime_for_general_greeting_token_match():
    runtime_context = {
        "knowledge_context": {
            "hits": [
                {
                    "item_key": "qa.production.answer",
                    "title": "生产知识闭环冒烟",
                    "answer_mode": "guided_answer",
                    "direct_answer": "生产知识闭环暗号是 canyon-lime。",
                    "customer_visible": True,
                }
            ],
            "locked_facts": [
                {
                    "item_key": "qa.production.answer",
                    "title": "生产知识闭环冒烟",
                    "answer_mode": "direct_answer",
                    "answer": "生产知识闭环暗号是 canyon-lime。",
                    "customer_visible": True,
                }
            ],
        }
    }

    latency_class = webchat_runtime_ai_service._latency_class_with_runtime_context(
        webchat_runtime_ai_service._latency_class_for_request(
            body="你好，普通客服测试 mr98twyq",
            evidence_present=False,
        ),
        body="你好，普通客服测试 mr98twyq",
        runtime_context=runtime_context,
        evidence_present=False,
    )

    assert latency_class == "unified_ai_runtime"


def test_latency_profile_keeps_real_ch_waybill_on_unified_runtime_path():
    latency_class = webchat_runtime_ai_service._latency_class_for_request(
        body="你好，帮我看看 CH020000129135",
        evidence_present=False,
    )

    assert latency_class == "unified_ai_runtime"


def test_runtime_profile_preserves_knowledge_context_in_unified_pipeline():
    runtime_context = {
        "knowledge_context": {
            "retrieval": "hybrid_rag_v2",
            "total_matches": 1,
            "hits": [{"item_key": "qa.production.answer"}],
            "locked_facts": [{"item_key": "qa.production.answer", "answer": "生产知识闭环暗号是 canyon-lime。"}],
        }
    }

    profiled = webchat_runtime_ai_service._runtime_context_with_latency_profile(
        runtime_context,
        latency_class="short_general_support",
    )

    assert profiled["latency_class"] == "unified_ai_runtime"
    assert profiled["runtime_prompt_profile"] == "unified_ai_runtime_v1"
    assert profiled["knowledge_context"]["retrieval"] == "hybrid_rag_v2"
    assert profiled["knowledge_context"]["locked_facts"]


def test_trusted_tracking_soft_accept_does_not_expose_json_reply():
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply='{"customer_reply":"Your parcel has been delivered.","language":"en","intent":"tracking","handoff_required":false}',
        intent="tracking",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={"pii_redacted": True, "fact_evidence_present": True, "tool_status": "success"},
        body="Please check CH020000007813",
    )

    assert result.ok is False
    assert result.reply is None
    assert result.error_code == "ai_decision_invalid_output"


def test_runtime_trace_preserves_contract_repair_attempt_budget():
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={"max_contract_repair_attempts": 2, "model": "qwen2.5:3b"},
        reply="Hi, how can I help?",
        intent="general_support",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(provider_result, body="hello")

    assert result.ok is True
    assert result.runtime_trace["max_contract_repair_attempts"] == 2
    assert result.runtime_trace["model"] == "qwen2.5:3b"


def test_safe_runtime_reply_is_not_silenced_by_bad_decision_side_fields(monkeypatch):
    def _raise_parse_error(*args, **kwargs):
        raise RuntimeReplyParseError("AI decision output is invalid: malformed non-visible control fields")

    monkeypatch.setattr(webchat_runtime_ai_service, "decision_from_provider_result", _raise_parse_error)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="瑞士目前暂未开通本对本业务。",
        intent="other",
        tracking_number=None,
        handoff_required=True,
        handoff_reason="invalid_control_field_should_not_trigger_handoff",
        recommended_agent_action="ignore",
        elapsed_ms=100,
    )

    result = _result_from_provider(provider_result, body="瑞士本地到本地现在支持寄送吗？")

    assert result.ok is True
    assert result.reply == "瑞士目前暂未开通本对本业务。"
    assert result.handoff_required is False
    assert result.recommended_agent_action is None
    assert result.runtime_trace == {
        "ai_decision_soft_accept_reason": "provider_reply_safe_decision_parse_failed"
    }


def test_runtime_result_polishes_waybill_term_before_webchat_write(monkeypatch):
    def _raise_parse_error(*args, **kwargs):
        raise RuntimeReplyParseError("AI decision output is invalid: malformed non-visible control fields")

    monkeypatch.setattr(webchat_runtime_ai_service, "decision_from_provider_result", _raise_parse_error)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="请确认一下这个waybill号码是否完整且正确，然后我将帮助您查询。",
        intent="tracking_unresolved",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(provider_result, body="我的包裹 CH020000129135 没收到，请人工介入")

    assert result.ok is True
    assert result.reply == "请确认一下这个运单号是否完整且正确，然后我将帮助您查询。"


def test_negative_tracking_lookup_policy_misfire_does_not_silence_runtime_reply(monkeypatch):
    def _policy_misfire(*args, **kwargs):
        return type("Policy", (), {"ok": False})(), {
            "policy_gate": {
                "ok": False,
                "violations": [{"code": "raw_tracking_exposed"}],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_misfire)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="我暂时未查到运单尾号 129135 的验证结果，请确认号码是否完整。",
        intent="tracking_unresolved",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "error",
            "pii_redacted": True,
            "tracking_fact_failure_reason": "tracking_lookup_no_match",
        },
        tracking_number="CH020000129135",
        body="请帮我查询运单 CH020000129135 的状态",
    )

    assert result.ok is True
    assert result.reply == "我暂时未查到运单尾号 129135 的验证结果，请确认号码是否完整。"
    assert result.runtime_trace == {
        "ai_decision_soft_accept_reason": "negative_tracking_lookup_policy_allow"
    }


def test_negative_tracking_lookup_allows_runtime_check_number_wording(monkeypatch):
    def _policy_misfire(*args, **kwargs):
        return type("Policy", (), {"ok": False})(), {
            "policy_gate": {
                "ok": False,
                "violations": [{"code": "raw_tracking_exposed"}],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_misfire)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="请确认一下运单号码是否完整正确。",
        intent="tracking_unresolved",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "error",
            "pii_redacted": True,
            "tracking_fact_failure_reason": "tracking_lookup_no_match",
        },
        tracking_number="CH020000129135",
        body="请帮我查询运单 CH020000129135 的状态",
    )

    assert result.ok is True
    assert result.reply == "请确认一下运单号码是否完整正确。"
    assert result.handoff_required is False


def test_negative_tracking_lookup_does_not_auto_handoff_without_customer_request(monkeypatch):
    def _policy_ok(*args, **kwargs):
        return type("Policy", (), {"ok": True})(), {
            "policy_gate": {
                "ok": True,
                "violations": [],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_ok)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="我暂时未查到该运单的验证结果，请确认号码是否完整正确。",
        intent="tracking_unresolved",
        tracking_number=None,
        handoff_required=True,
        handoff_reason="tracking_not_found",
        recommended_agent_action="Take over the conversation.",
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "error",
            "pii_redacted": True,
            "tracking_fact_failure_reason": "tracking_lookup_no_match",
        },
        tracking_number="CH020000129135",
        body="请帮我查询运单 CH020000129135 的状态",
    )

    assert result.ok is True
    assert result.handoff_required is False
    assert result.handoff_reason is None
    assert result.recommended_agent_action is None
    assert result.runtime_trace == {
        "ai_decision_control_override_reason": "negative_tracking_lookup_no_auto_handoff"
    }


def test_explicit_customer_handoff_request_creates_control_signal(monkeypatch):
    def _policy_ok(*args, **kwargs):
        return type("Policy", (), {"ok": True})(), {
            "policy_gate": {
                "ok": True,
                "violations": [],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_ok)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="我暂时未查到该运单的验证结果，请确认号码是否完整正确。",
        intent="tracking_unresolved",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "error",
            "pii_redacted": True,
            "tracking_fact_failure_reason": "tracking_lookup_no_match",
        },
        tracking_number="CH020000129135",
        body="我的包裹 CH020000129135 收件人一直没收到，请人工介入处理",
    )

    assert result.ok is True
    assert result.reply == "我暂时未查到该运单的验证结果，请确认号码是否完整正确。"
    assert result.handoff_required is True
    assert result.handoff_reason == "customer_requested_human_review"
    assert result.recommended_agent_action
    assert result.runtime_trace == {
        "ai_decision_control_override_reason": "explicit_customer_handoff_request"
    }


def test_trusted_tracking_safe_suffix_control_field_does_not_block_handoff(monkeypatch):
    def _policy_ok(*args, **kwargs):
        return type("Policy", (), {"ok": True})(), {
            "policy_gate": {
                "ok": True,
                "violations": [],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_ok)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="I understand your concern. A human agent will be routed to assist you with this parcel.",
        intent="other",
        tracking_number="parcel ending 129135",
        handoff_required=True,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "success",
            "pii_redacted": True,
            "fact_evidence_present": True,
        },
        tracking_number="CH020000129135",
        body="I need a human agent to help with parcel CH020000129135",
    )

    assert result.ok is True
    assert result.reply == "I understand your concern. A human agent will be routed to assist you with this parcel."
    assert result.handoff_required is True


def test_trusted_tracking_ordinary_status_does_not_auto_handoff(monkeypatch):
    def _policy_ok(*args, **kwargs):
        return type("Policy", (), {"ok": True})(), {
            "policy_gate": {
                "ok": True,
                "violations": [],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_ok)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="Your parcel is currently pending pickup. I will now route this to a human agent who can provide more detailed information.",
        intent="tracking",
        tracking_number="parcel ending 129135",
        handoff_required=True,
        handoff_reason="other_requires_human_review",
        recommended_agent_action="Take over the conversation.",
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "success",
            "pii_redacted": True,
            "fact_evidence_present": True,
            "status_context": {"code": "10", "label": "pending pickup"},
            "tracking_lifecycle": {"risk": {"escalate_required": False}},
        },
        tracking_number="CH020000129135",
        body="can you please check my parcel CH020000129135",
    )

    assert result.ok is True
    assert result.reply == "Your parcel is currently pending pickup."
    assert result.handoff_required is False
    assert result.handoff_reason is None
    assert result.recommended_agent_action is None
    assert result.runtime_trace == {
        "ai_decision_control_override_reason": "trusted_tracking_no_auto_handoff"
    }


def test_trusted_tracking_status_context_can_require_handoff(monkeypatch):
    def _policy_ok(*args, **kwargs):
        return type("Policy", (), {"ok": True})(), {
            "policy_gate": {
                "ok": True,
                "violations": [],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_ok)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="Your parcel has a customs exception. A support agent will assist with the next step.",
        intent="tracking",
        tracking_number="parcel ending 129135",
        handoff_required=True,
        handoff_reason="customs_exception_requires_human_review",
        recommended_agent_action="Review customs exception.",
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "success",
            "pii_redacted": True,
            "fact_evidence_present": True,
            "status_context": {"code": "401", "label": "customs exception", "needs_human_review": True},
            "tracking_lifecycle": {"risk": {"escalate_required": True}},
        },
        tracking_number="CH020000129135",
        body="can you please check my parcel CH020000129135",
    )

    assert result.ok is True
    assert result.reply == "Your parcel has a customs exception. A support agent will assist with the next step."
    assert result.handoff_required is True
    assert result.handoff_reason == "customs_exception_requires_human_review"


def test_trusted_tracking_safe_suffix_control_field_is_not_treated_as_raw_identifier():
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={},
        reply="Your parcel with tracking reference parcel ending 129135 is currently in the pending pickup stage.",
        intent="tracking",
        tracking_number="parcel ending 129135",
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={
            "tool_status": "success",
            "pii_redacted": True,
            "fact_evidence_present": True,
        },
        tracking_number="CH020000129135",
        body="can you please check my parcel CH020000129135",
    )

    assert result.ok is True
    assert result.reply == "Your parcel with tracking reference parcel ending 129135 is currently in the pending pickup stage."
    assert result.error_code is None


def test_policy_blocked_result_keeps_safe_policy_trace(monkeypatch):
    def _policy_misfire(*args, **kwargs):
        return type("Policy", (), {"ok": False})(), {
            "policy_gate": {
                "ok": False,
                "violations": [{"code": "raw_tracking_exposed"}],
                "warnings": ["trusted tracking fact hash does not match extracted tracking number hash"],
                "checked_tools": ["speedaf.order.query"],
            },
            "decision": {
                "intent": "tracking",
                "next_action": "reply",
                "handoff_required": False,
            },
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_misfire)
    provider_result = RuntimeAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="private_ai_runtime",
        raw_provider="private_ai_runtime",
        raw_payload_safe_summary={"model": "qwen2.5:3b"},
        reply="Your parcel CH020000129135 is currently pending pickup.",
        intent="tracking",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        elapsed_ms=100,
    )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata={"tool_status": "success", "pii_redacted": True, "fact_evidence_present": True},
        tracking_number="CH020000129135",
        body="can you please check my parcel CH020000129135",
    )

    assert result.ok is False
    assert result.error_code == "ai_decision_policy_blocked"
    assert result.runtime_trace == {
        "ai_decision_policy_ok": False,
        "ai_decision_policy_violation_codes": "raw_tracking_exposed",
        "ai_decision_policy_warning_count": 1,
        "ai_decision_checked_tools": "speedaf.order.query",
        "ai_decision_intent": "tracking",
        "ai_decision_next_action": "reply",
        "ai_decision_handoff_required": False,
        "error_code": "ai_decision_policy_blocked",
        "model": "qwen2.5:3b",
    }


@pytest.mark.asyncio
async def test_generate_preserves_negative_tracking_metadata_for_policy_soft_allow(monkeypatch):
    async def _dispatch(request):
        assert request.tracking_fact_evidence_present is False
        assert request.tracking_fact_metadata["tool_status"] == "error"
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={},
            reply="我暂时未查到运单尾号 129135 的验证结果，请确认号码是否完整。",
            intent="tracking_unresolved",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=100,
        )

    def _policy_misfire(*, tracking_fact_metadata=None, **_kwargs):
        assert tracking_fact_metadata["tool_status"] == "error"
        return type("Policy", (), {"ok": False})(), {
            "policy_gate": {
                "ok": False,
                "violations": [{"code": "raw_tracking_exposed"}],
            }
        }

    monkeypatch.setattr(webchat_runtime_ai_service, "_runtime_context_for_request", lambda **_kwargs: {})
    monkeypatch.setattr(webchat_runtime_ai_service, "dispatch_webchat_runtime_reply", _dispatch)
    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_misfire)

    result = await webchat_runtime_ai_service.generate_webchat_runtime_reply(
        tenant_key="default",
        channel_key="website",
        session_id="wc-test",
        body="请帮我查询运单 CH020000129135 的状态",
        recent_context=[],
        request_id="test-negative-lookup",
        tracking_fact_summary=None,
        tracking_fact_metadata={
            "tool_status": "error",
            "pii_redacted": True,
            "tracking_fact_failure_reason": "tracking_lookup_no_match",
        },
        tracking_fact_evidence_present=False,
    )

    assert result.ok is True
    assert result.reply == "我暂时未查到运单尾号 129135 的验证结果，请确认号码是否完整。"
    assert result.runtime_trace == {
        "ai_decision_soft_accept_reason": "negative_tracking_lookup_policy_allow"
    }


@pytest.mark.asyncio
async def test_generate_trusted_tracking_uses_light_runtime_profile(monkeypatch):
    captured = {}

    async def _dispatch(request):
        captured["request"] = request
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={
                "latency_class": request.metadata["latency_class"],
                "prompt_profile": request.metadata["runtime_prompt_profile"],
                "runtime_usage": {
                    "total_duration_ms": 1400,
                    "eval_duration_ms": 900,
                    "eval_count": 18,
                },
            },
            reply="Your parcel is currently pending pickup.",
            intent="tracking",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=100,
        )

    monkeypatch.setattr(
        webchat_runtime_ai_service,
        "_runtime_context_for_request",
        lambda **_kwargs: {"knowledge_context": {"hits": [{"item_key": "internal"}]}, "domain_intelligence_trace": {"used": True}},
    )
    monkeypatch.setattr(webchat_runtime_ai_service, "dispatch_webchat_runtime_reply", _dispatch)

    result = await webchat_runtime_ai_service.generate_webchat_runtime_reply(
        tenant_key="default",
        channel_key="website",
        session_id="wc-test",
        body="can you please check my parcel CH020000129135",
        recent_context=[{"role": "customer", "text": "old context"}],
        request_id="test-trusted-tracking-light-profile",
        tracking_fact_summary=(
            "Trusted tracking fact:\n"
            "- Tracking reference: parcel ending 129135\n"
            "- Current status: pending pickup"
        ),
        tracking_fact_metadata={"tool_status": "success", "pii_redacted": True, "fact_evidence_present": True},
        tracking_fact_evidence_present=True,
    )

    request = captured["request"]
    assert result.ok is True
    assert request.recent_context == [{"role": "customer", "text": "old context"}]
    assert request.metadata["latency_class"] == "trusted_tracking_fact"
    assert request.metadata["runtime_prompt_profile"] == "trusted_tracking_fact_v1"
    assert request.metadata["context_version"] == "nexus_webchat_runtime_context_v1"
    assert request.metadata["knowledge_context"] == {}
    assert request.metadata["retrieval_methods"] == ["skipped_for_trusted_tracking_fact"]
    assert result.runtime_trace["runtime_usage"] == {
        "total_duration_ms": 1400,
        "eval_duration_ms": 900,
        "eval_count": 18,
    }


@pytest.mark.asyncio
async def test_generate_knowledge_answer_uses_unified_runtime_profile(monkeypatch):
    captured = {}

    async def _dispatch(request):
        captured["request"] = request
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={
                "latency_class": request.metadata["latency_class"],
                "prompt_profile": request.metadata["runtime_prompt_profile"],
            },
            reply="Switzerland domestic-to-domestic service is currently unavailable.",
            intent="service_or_policy",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=100,
        )

    monkeypatch.setattr(
        webchat_runtime_ai_service,
        "_runtime_context_for_request",
        lambda **_kwargs: {
            "knowledge_context": {
                "hits": [
                    {
                        "item_key": "nexus.support.customer.kb.ch.service.availability",
                        "answer_mode": "direct_answer",
                        "direct_answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                        "customer_visible": True,
                    }
                ],
                "locked_facts": [
                    {
                        "item_key": "nexus.support.customer.kb.ch.service.availability",
                        "answer_mode": "direct_answer",
                        "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                        "customer_visible": True,
                    }
                ],
            },
            "domain_intelligence_trace": {"used": True},
        },
    )
    monkeypatch.setattr(webchat_runtime_ai_service, "dispatch_webchat_runtime_reply", _dispatch)

    result = await webchat_runtime_ai_service.generate_webchat_runtime_reply(
        tenant_key="default",
        channel_key="website",
        session_id="wc-test",
        body="Do you provide domestic-to-domestic delivery in Switzerland?",
        recent_context=[{"role": "customer", "text": "old context"}],
        request_id="test-knowledge-direct-answer-light-profile",
    )

    request = captured["request"]
    assert result.ok is True
    assert request.recent_context == [{"role": "customer", "text": "old context"}]
    assert request.metadata["latency_class"] == "unified_ai_runtime"
    assert request.metadata["runtime_prompt_profile"] == "unified_ai_runtime_v1"
    assert request.metadata["knowledge_context"]["locked_facts"]


@pytest.mark.asyncio
async def test_generate_explicit_handoff_request_uses_unified_runtime_profile(monkeypatch):
    captured = {}

    async def _dispatch(request):
        captured["request"] = request
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={
                "latency_class": request.metadata["latency_class"],
                "prompt_profile": request.metadata["runtime_prompt_profile"],
            },
            reply="I understand. Human support will review this conversation.",
            intent="handoff",
            tracking_number=None,
            handoff_required=True,
            handoff_reason="customer_requested_human_review",
            recommended_agent_action="Review and take over.",
            elapsed_ms=100,
        )

    monkeypatch.setattr(
        webchat_runtime_ai_service,
        "_runtime_context_for_request",
        lambda **_kwargs: {"knowledge_context": {"hits": [{"item_key": "must-not-enter"}]}, "domain_intelligence_trace": {"used": True}},
    )
    monkeypatch.setattr(webchat_runtime_ai_service, "dispatch_webchat_runtime_reply", _dispatch)

    result = await webchat_runtime_ai_service.generate_webchat_runtime_reply(
        tenant_key="default",
        channel_key="website",
        session_id="wc-test",
        body="I need a human agent for this issue",
        recent_context=[{"role": "customer", "text": "older context should not be needed"}],
        request_id="test-explicit-handoff-light-profile",
    )

    request = captured["request"]
    assert result.ok is True
    assert request.recent_context == [{"role": "customer", "text": "older context should not be needed"}]
    assert request.metadata["latency_class"] == "unified_ai_runtime"
    assert request.metadata["runtime_prompt_profile"] == "unified_ai_runtime_v1"
    assert request.metadata["context_version"] == "nexus_webchat_runtime_context_v1"
    assert request.metadata["knowledge_context"]["hits"] == [{"item_key": "must-not-enter"}]
    assert request.metadata["domain_intelligence_trace"] == {"used": True}


@pytest.mark.asyncio
async def test_generate_trusted_tracking_repairs_raw_identifier_locally(monkeypatch):
    calls = []
    policy_calls = []

    async def _dispatch(request):
        calls.append(request)
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={
                "latency_class": request.metadata["latency_class"],
                "prompt_profile": request.metadata["runtime_prompt_profile"],
            },
            reply="Your parcel CH020000129135 is currently pending pickup.",
            intent="tracking",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=100,
        )

    def _policy_blocks_then_allows(*_args, **_kwargs):
        policy_calls.append(_args)
        if len(policy_calls) == 1:
            return type("Policy", (), {"ok": False})(), {
                "policy_gate": {
                    "ok": False,
                    "violations": [{"code": "raw_tracking_exposed"}],
                }
            }
        return type("Policy", (), {"ok": True})(), {
            "policy_gate": {
                "ok": True,
                "violations": [],
            }
        }

    monkeypatch.setattr(
        webchat_runtime_ai_service,
        "_runtime_context_for_request",
        lambda **_kwargs: {"knowledge_context": {"hits": []}, "domain_intelligence_trace": {"used": True}},
    )
    monkeypatch.setattr(webchat_runtime_ai_service, "dispatch_webchat_runtime_reply", _dispatch)
    monkeypatch.setattr(webchat_runtime_ai_service, "validate_and_trace_decision", _policy_blocks_then_allows)

    result = await webchat_runtime_ai_service.generate_webchat_runtime_reply(
        tenant_key="default",
        channel_key="website",
        session_id="wc-test",
        body="can you please check my parcel CH020000129135",
        recent_context=[],
        request_id="test-trusted-tracking-local-privacy-repair",
        tracking_fact_summary=(
            "Trusted tracking fact:\n"
            "- Tracking reference: parcel ending 129135\n"
            "- Current status: pending pickup"
        ),
        tracking_fact_metadata={
            "tool_status": "success",
            "pii_redacted": True,
            "fact_evidence_present": True,
            "tracking_number": "CH020000129135",
        },
        tracking_fact_evidence_present=True,
    )

    assert len(calls) == 1
    assert len(policy_calls) == 2
    assert result.ok is True
    assert result.reply_source == "private_ai_runtime:repaired"
    assert result.reply == "Your parcel ending 129135 is currently pending pickup."
    assert "CH020000129135" not in result.reply
