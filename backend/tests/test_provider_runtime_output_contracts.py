import pytest
from app.services.provider_runtime.output_contracts import OutputContracts

def test_speedaf_webchat_fast_reply_v1_valid():
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false, "ticket_should_create": false}'
    parsed = OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)
    assert parsed["customer_reply"] == "hello"

def test_speedaf_webchat_fast_reply_v1_invalid():
    # missing ticket_should_create
    raw_json = '{"customer_reply": "hello", "language": "en", "intent": "greeting", "handoff_required": false}'
    with pytest.raises(ValueError, match="Missing required field: ticket_should_create"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", raw_json)

def test_invalid_json():
    with pytest.raises(ValueError, match="Output must be valid JSON"):
        OutputContracts.validate_and_parse("speedaf_webchat_fast_reply_v1", "not json")
