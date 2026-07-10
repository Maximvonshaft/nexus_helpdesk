from __future__ import annotations

import pytest

from app.services.knowledge_runtime_v2 import runtime


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


def test_tracking_intent_guard_is_installed_on_runtime():
    assert runtime.is_live_tracking_intent.__module__.endswith("tracking_intent_guard")
