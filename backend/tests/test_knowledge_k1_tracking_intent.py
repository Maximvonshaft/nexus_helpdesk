from __future__ import annotations

import pytest

from app.services.knowledge_runtime_v2 import runtime


_NO_EVIDENCE_EXPANSION_SUFFIX = " ".join(
    [
        "tracking lookup failed",
        "waybill not found",
        "wrong tracking number",
        "tracking number format",
        "waybill format",
        "客户输入运单号查不到",
        "订单号多输少输",
        "运单号格式",
        "核对单号",
        "CH tracking number format",
    ]
)


@pytest.mark.parametrize(
    "query",
    [
        "What is the tracking status policy?",
        "What is the policy for delivered packages?",
        "What is the rule for shipment status?",
        "Was ist die Richtlinie für den Sendungsstatus?",
        "Quelle est la politique du statut de suivi ?",
        "运单状态政策是什么？",
        "包裹签收规则是什么？",
    ],
)
def test_static_tracking_policy_questions_remain_eligible_for_knowledge(query):
    assert runtime.is_live_tracking_intent(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "Where is parcel CH120000005451 now?",
        "What is the current status of CH120000005451?",
        "Where is my package?",
        "我的包裹现在到哪里了？",
        "Wo ist mein Paket?",
        "Où est mon colis ?",
        "¿Dónde está mi paquete?",
        "Gdje je moj paket?",
        "What is the policy status for parcel CH120000005451?",
    ],
)
def test_identifier_or_explicit_current_location_routes_to_tracking_truth(query):
    assert runtime.is_live_tracking_intent(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "What is the tracking number format?",
        "运单号格式是什么？",
        "CH120000005451",
        "Example waybill number",
    ],
)
def test_format_guidance_and_identifier_only_do_not_trigger_live_tracking(query):
    assert runtime.is_live_tracking_intent(query) is False


@pytest.mark.parametrize(
    "customer_prefix",
    [
        "CH1200000011425 CH1200000011425",
        "请帮我看看 CH020000129135",
    ],
)
def test_internal_no_evidence_expansion_does_not_become_live_intent(customer_prefix):
    expanded_retrieval_query = f"{customer_prefix} {_NO_EVIDENCE_EXPANSION_SUFFIX}"

    assert runtime.is_live_tracking_intent(expanded_retrieval_query) is False


@pytest.mark.parametrize(
    "customer_prefix",
    [
        "Where is parcel CH120000005451 now? CH120000005451",
        "What is the current status of CH120000005451? CH120000005451",
        "我的包裹 CH120000005451 现在到哪里了？ CH120000005451",
    ],
)
def test_internal_no_evidence_expansion_preserves_true_live_intent(customer_prefix):
    expanded_retrieval_query = f"{customer_prefix} {_NO_EVIDENCE_EXPANSION_SUFFIX}"

    assert runtime.is_live_tracking_intent(expanded_retrieval_query) is True


def test_tracking_intent_guard_is_installed_on_runtime():
    assert runtime.is_live_tracking_intent.__module__.endswith("tracking_intent_guard")
