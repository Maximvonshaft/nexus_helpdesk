from __future__ import annotations

from app.services.domain_intelligence import (
    ActionBoundary,
    AnswerPlanType,
    EvidenceClass,
    RankedCandidate,
    filter_allowed_candidates,
    plan_answer,
    rerank_candidates,
    understand_query,
)


def test_low_signal_query_has_no_business_intent() -> None:
    result = understand_query("hello")
    assert result.domain is None
    assert result.primary_intent is None
    assert not result.has_business_intent
    assert plan_answer(result).plan_type == AnswerPlanType.SAFE_GENERAL_REPLY


def test_recipient_absent_maps_to_delivery_attempt() -> None:
    result = understand_query("The courier arrived while I was not home. Will you deliver again?")
    assert result.domain == "logistics"
    assert result.primary_intent in {
        "logistics.delivery_attempt_failed.recipient_absent",
        "logistics.redelivery",
    }
    assert result.rewrite is not None
    assert result.action_boundary == ActionBoundary.NONE


def test_chinese_address_change_requires_verification() -> None:
    result = understand_query("地址写错了，可以改地址吗？")
    assert result.primary_intent == "logistics.address_change"
    assert result.requires_verification is True
    assert result.requires_tool_boundary is True
    plan = plan_answer(result)
    assert plan.plan_type == AnswerPlanType.TOOL_PREPARE


def test_tracking_query_maps_to_tool_boundary() -> None:
    result = understand_query("Where is my parcel now?")
    assert result.primary_intent == "logistics.tracking_status"
    assert result.evidence_class == EvidenceClass.LIVE_STATUS
    assert result.requires_tool_boundary is True
    assert plan_answer(result).plan_type == AnswerPlanType.TOOL_CALL


def test_reranker_prefers_matching_domain_and_intent() -> None:
    result = understand_query("I was not at home when courier arrived")
    weak = RankedCandidate(item_key="generic-fee", score=30, domain="logistics", intent_keys=("logistics.pricing",), evidence_class=EvidenceClass.FAQ)
    strong = RankedCandidate(item_key="missed-delivery-policy", score=10, domain="logistics", intent_keys=("logistics.delivery_attempt_failed.recipient_absent",), evidence_class=EvidenceClass.POLICY)
    ranked = rerank_candidates([weak, strong], result)
    assert ranked[0].item_key == "missed-delivery-policy"


def test_domain_guard_blocks_wrong_intent_candidate() -> None:
    result = understand_query("I was not home when courier arrived")
    candidate = RankedCandidate(item_key="generic-fee", score=100, domain="logistics", intent_keys=("logistics.pricing",), evidence_class=EvidenceClass.FAQ)
    allowed, decisions = filter_allowed_candidates([candidate], result)
    assert allowed == []
    assert decisions[0].reason == "wrong_intent"
