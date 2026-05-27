import json

import pytest

from app.services.provider_runtime.output_contracts import OutputContracts


def _approved_direct_answer_context(answer: str) -> dict:
    source = {"item_key": "fact.ch.shipping-sla"}
    return {
        "locked_facts": [
            {
                "id": "fact.ch.shipping-sla#0",
                "answer": answer,
                "mode": "locked_fact",
                "source": source,
            }
        ],
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
                "source_metadata": source,
            }
        ]
    }


def test_speedaf_webchat_fast_reply_v1_valid():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    parsed = OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)
    assert parsed["customer_reply"] == "hello"


def test_speedaf_webchat_fast_reply_v1_invalid_schema():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false}'
    with pytest.raises(ValueError, match="Schema validation failed"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)


def test_speedaf_webchat_fast_reply_v1_additional_props():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false, "fake_prop": 1}'
    with pytest.raises(ValueError, match="Schema validation failed"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)


def test_invalid_json():
    with pytest.raises(ValueError, match="Output must be valid JSON"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", "not json")


def test_security_markdown():
    raw_json = '{"customer_reply": "```json\\nhello\\n```", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="Markdown code blocks are prohibited"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)


def test_security_reasoning():
    raw_json = '{"customer_reply": "<think>test</think>", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="Hidden reasoning is prohibited"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)


def test_security_secret_leakage():
    prefix = "ey" + "J"
    raw_json = '{"customer_reply": "' + prefix + 'abcdefghijklmno", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="Potential secret leakage detected"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)


def test_tracking_intent_requires_trusted_evidence():
    raw_json = '{"customer_reply": "Your parcel is in transit.", "language": "en", "intent": "tracking", "tracking_number": "ABC123", "handoff_required": false, "ticket_should_create": false}'
    with pytest.raises(ValueError, match="requires trusted tracking evidence"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json, evidence_present=False)
    parsed = OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json, evidence_present=True)
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
        "speedaf_webchat_fast_reply_v1",
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
            "speedaf_webchat_fast_reply_v1",
            raw_json,
            evidence_present=False,
            request_body="瑞士海运时效是多少？",
            knowledge_context=_approved_direct_answer_context("瑞士海运清关时效为 15 天。"),
        )


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("瑞士海运清关通常需要 15 天。", "pass"),
        ("瑞士海运清关通常需要 20 天。", "Locked fact numeric conflict"),
        ("尼日利亚海运清关通常需要 15 天。", "Locked fact entity conflict"),
        ("瑞士空运清关通常需要 15 天。", "Locked fact service conflict"),
    ],
)
def test_locked_facts_validate_provider_generated_fact_equivalence(reply, expected):
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
    context = _approved_direct_answer_context("瑞士海运清关时效为 15 天。")

    if expected == "pass":
        parsed = OutputContracts.validate_and_parse(
            "speedaf_webchat_fast_reply_v1",
            raw_json,
            evidence_present=False,
            request_body="瑞士海运时效是多少？",
            knowledge_context=context,
        )
        assert parsed["customer_reply"] == reply
    else:
        with pytest.raises(ValueError, match=expected):
            OutputContracts.validate_and_parse(
                "speedaf_webchat_fast_reply_v1",
                raw_json,
                evidence_present=False,
                request_body="瑞士海运时效是多少？",
                knowledge_context=context,
            )


def test_locked_facts_reject_service_number_swap():
    raw_json = json.dumps(
        {
            "customer_reply": "海运10天，空运15天。",
            "language": "zh",
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="Locked fact service conflict"):
        OutputContracts.validate_and_parse(
            "speedaf_webchat_fast_reply_v1",
            raw_json,
            evidence_present=False,
            request_body="海运和空运多久？",
            knowledge_context=_approved_direct_answer_context("海运15天，空运10天。"),
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
            "speedaf_webchat_fast_reply_v1",
            raw_json,
            evidence_present=False,
            request_body="瑞士海运时效是多少？",
            knowledge_context=_approved_direct_answer_context("瑞士海运清关时效为 15 天。"),
        )
