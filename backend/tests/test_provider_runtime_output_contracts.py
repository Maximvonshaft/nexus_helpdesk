import json

import pytest

from app.services.provider_runtime.output_contracts import OutputContracts


def _approved_direct_answer_context(answer: str) -> dict:
    return {
        "hits": [
            {
                "item_key": "fact.ch.shipping-sla",
                "title": "瑞士海运时效",
                "score": 42.0,
                "chunk_index": 0,
                "retrieval_method": "structured_fact_recall+direct_answer_fact",
                "direct_answer": answer,
                "answer_mode": "direct_answer",
                "metadata": {
                    "knowledge_kind": "business_fact",
                    "fact_status": "approved",
                    "answer_mode": "direct_answer",
                },
                "source_metadata": {"item_key": "fact.ch.shipping-sla"},
            }
        ]
    }


def _locked_fact_context(answer: str) -> dict:
    context = _approved_direct_answer_context(answer)
    context["locked_facts"] = [
        {
            "item_key": "fact.ng.shipping-sla",
            "title": "尼日利亚海运时效",
            "question": "尼日利亚海运时效是多少？",
            "answer": answer,
            "answer_mode": "direct_answer",
            "source": {"item_key": "fact.ng.shipping-sla", "title": "尼日利亚海运时效"},
        }
    ]
    return context


def test_webchat_runtime_reply_valid():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    parsed = OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json)
    assert parsed["customer_reply"] == "hello"


def test_webchat_runtime_reply_invalid_schema():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false}'
    with pytest.raises(ValueError, match="Schema validation failed"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json)


def test_webchat_runtime_reply_additional_props():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false, "fake_prop": 1}'
    with pytest.raises(ValueError, match="Schema validation failed"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json)


def test_invalid_json():
    with pytest.raises(ValueError, match="Output must be valid JSON"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", "not json")


def test_unknown_output_contract_is_rejected():
    with pytest.raises(ValueError, match="Unsupported output contract"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply.retired", "{}")


def test_security_markdown():
    raw_json = '{"customer_reply": "```json\\nhello\\n```", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="Markdown code blocks are prohibited"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json)


def test_security_reasoning():
    raw_json = '{"customer_reply": "<think>test</think>", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="Hidden reasoning is prohibited"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json)


def test_security_secret_leakage():
    prefix = "ey" + "J"
    raw_json = '{"customer_reply": "' + prefix + 'abcdefghijklmno", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="Potential secret leakage detected"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json)


def test_tracking_intent_requires_trusted_evidence():
    raw_json = '{"customer_reply": "Your parcel is in transit.", "language": "en", "intent": "tracking", "tracking_number": "ABC123", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="requires trusted tracking evidence"):
        OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json, evidence_present=False)
    parsed = OutputContracts.validate_and_parse("nexus.webchat_runtime_reply", raw_json, evidence_present=True)
    assert parsed["tracking_number"] == "ABC123"


def test_business_sla_direct_answer_status_words_pass_with_approved_grounding():
    answer = "瑞士海运清关时效为 15 天。"
    raw_json = json.dumps(
        {
            "customer_reply": answer,
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    parsed = OutputContracts.validate_and_parse(
        "nexus.webchat_runtime_reply",
        raw_json,
        evidence_present=False,
        request_body="瑞士海运时效是多少？",
        knowledge_context=_approved_direct_answer_context(answer),
    )

    assert parsed["customer_reply"] == answer


def test_live_parcel_status_still_fails_without_trusted_tracking_evidence():
    raw_json = json.dumps(
        {
            "customer_reply": "你的包裹正在运输中。",
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Parcel status language requires trusted tracking evidence"):
        OutputContracts.validate_and_parse(
            "nexus.webchat_runtime_reply",
            raw_json,
            evidence_present=False,
            request_body="瑞士海运时效是多少？",
            knowledge_context=_approved_direct_answer_context("瑞士海运清关时效为 15 天。"),
        )


def test_direct_answer_does_not_excuse_extra_live_parcel_status_claim():
    raw_json = json.dumps(
        {
            "customer_reply": "瑞士海运清关时效为 15 天。你的包裹正在运输中。",
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Parcel status language requires trusted tracking evidence"):
        OutputContracts.validate_and_parse(
            "nexus.webchat_runtime_reply",
            raw_json,
            evidence_present=False,
            request_body="瑞士海运时效是多少？",
            knowledge_context=_approved_direct_answer_context("瑞士海运清关时效为 15 天。"),
        )


def test_locked_fact_equivalent_natural_reply_passes():
    raw_json = json.dumps(
        {
            "customer_reply": "尼日利亚海运通常需要 15 天。",
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    parsed = OutputContracts.validate_and_parse(
        "nexus.webchat_runtime_reply",
        raw_json,
        evidence_present=False,
        request_body="尼日利亚海运时效是多少？",
        knowledge_context=_locked_fact_context("尼日利亚海运时效为 15 天。"),
    )

    assert parsed["customer_reply"] == "尼日利亚海运通常需要 15 天。"


def test_locked_fact_rejects_question_echo_without_answer_specific_fact():
    raw_json = json.dumps(
        {
            "customer_reply": "您提到的MCS唯一事实编号mr9ebkzk是什么意思？我可以帮您查找相关信息。",
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Locked fact grounding conflict"):
        OutputContracts.validate_and_parse(
            "nexus.webchat_runtime_reply",
            raw_json,
            evidence_present=False,
            request_body="请告诉我MCS唯一事实编号mr9ebkzk",
            knowledge_context=_locked_fact_context("MCS唯一事实编号mr9ebkzk对应的知识闭环结果是 PACE。"),
        )


@pytest.mark.parametrize(
    "reply",
    [
        "尼日利亚海运通常需要 20 天。",
        "瑞士海运时效为 15 天。",
        "尼日利亚空运时效为 15 天。",
    ],
)
def test_locked_fact_conflicts_are_rejected(reply):
    raw_json = json.dumps(
        {
            "customer_reply": reply,
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Locked fact grounding conflict"):
        OutputContracts.validate_and_parse(
            "nexus.webchat_runtime_reply",
            raw_json,
            evidence_present=False,
            request_body="尼日利亚海运时效是多少？",
            knowledge_context=_locked_fact_context("尼日利亚海运时效为 15 天。"),
        )


def test_any_locked_fact_conflict_rejects_even_when_another_fact_matches():
    context = {
        "locked_facts": [
            {
                "item_key": "nexus.support.customer.kb.ch.service.availability",
                "answer": "Switzerland domestic-to-domestic service is currently unavailable. 瑞士目前暂未开通本对本业务。",
                "source": {"item_key": "nexus.support.customer.kb.ch.service.availability"},
            },
            {
                "item_key": "prod.global.tracking-number.required",
                "answer": "To check parcel status, the customer must provide a tracking or waybill number.",
                "source": {"item_key": "prod.global.tracking-number.required"},
            },
        ]
    }
    raw_json = json.dumps(
        {
            "customer_reply": "Sure, we provide domestic delivery services within Switzerland. Please provide the tracking number.",
            "language": "en",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Locked fact grounding conflict"):
        OutputContracts.validate_and_parse(
            "nexus.webchat_runtime_reply",
            raw_json,
            evidence_present=False,
            request_body="Do you provide domestic to domestic delivery in Switzerland?",
            knowledge_context=context,
        )


def test_mixed_language_locked_fact_allows_matching_customer_language_reply():
    raw_json = json.dumps(
        {
            "customer_reply": "瑞士目前暂未开通本对本业务。",
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    parsed = OutputContracts.validate_and_parse(
        "nexus.webchat_runtime_reply",
        raw_json,
        evidence_present=False,
        request_body="瑞士本地到本地现在支持寄送吗？",
        knowledge_context=_locked_fact_context(
            "Switzerland domestic-to-domestic service is currently unavailable. "
            "Switzerland domestic-to-domestic service availability 瑞士目前暂未开通本对本业务。"
        ),
    )

    assert parsed["customer_reply"] == "瑞士目前暂未开通本对本业务。"


def test_trusted_tracking_followup_bypasses_unrelated_locked_fact():
    context = {
        "locked_facts": [
            {
                "item_key": "nexus.support.customer.kb.ch.service.availability",
                "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                "source": {"item_key": "nexus.support.customer.kb.ch.service.availability"},
            }
        ]
    }
    raw_json = json.dumps(
        {
            "customer_reply": "Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception or the delivery contact point, then ask us for human review.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": "CH020000007813",
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    parsed = OutputContracts.validate_and_parse(
        "nexus.webchat_runtime_reply",
        raw_json,
        evidence_present=True,
        request_body="The recipient says they did not receive it. What should we do?",
        knowledge_context=context,
    )

    assert parsed["customer_reply"].startswith("Your parcel ending 007813")


def test_policy_question_still_obeys_locked_fact_with_stale_tracking_evidence():
    context = {
        "locked_facts": [
            {
                "item_key": "nexus.support.customer.kb.ch.service.availability",
                "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                "source": {"item_key": "nexus.support.customer.kb.ch.service.availability"},
            }
        ]
    }
    raw_json = json.dumps(
        {
            "customer_reply": "Yes, we support domestic-to-domestic delivery in Switzerland.",
            "language": "en",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Locked fact grounding conflict"):
        OutputContracts.validate_and_parse(
            "nexus.webchat_runtime_reply",
            raw_json,
            evidence_present=True,
            request_body="Do you provide domestic to domestic delivery in Switzerland?",
            knowledge_context=context,
        )
