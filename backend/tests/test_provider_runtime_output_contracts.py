import pytest

from app.services.provider_runtime.output_contracts import OutputContracts


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
