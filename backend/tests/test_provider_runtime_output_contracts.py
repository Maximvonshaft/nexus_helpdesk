from __future__ import annotations

import json

import pytest

from app.services.ai_reply_contract import (
    build_ai_reply_contract,
    contract_validation_args_from_payload,
    validate_ai_reply_contract,
)
from app.services.provider_runtime.output_contracts import (
    AGENT_TURN_OUTPUT_CONTRACT,
    OutputContracts,
)


def test_agent_turn_final_reply_is_valid():
    parsed = OutputContracts.validate_and_parse(
        AGENT_TURN_OUTPUT_CONTRACT,
        json.dumps(
            {
                "customer_reply": "Hello, how can I help?",
                "intent": "general_support",
                "next_action": "reply",
                "handoff_required": False,
                "tool_calls": [],
            }
        ),
    )
    assert parsed["customer_reply"] == "Hello, how can I help?"


def test_agent_turn_tool_request_is_valid_without_customer_reply():
    parsed = OutputContracts.validate_and_parse(
        AGENT_TURN_OUTPUT_CONTRACT,
        json.dumps(
            {
                "customer_reply": None,
                "intent": "shipment_tracking",
                "next_action": "call_tool",
                "handoff_required": False,
                "tool_calls": [
                    {
                        "tool_name": "speedaf.order.query",
                        "arguments": {"tracking_number": "CH020000129135"},
                    }
                ],
            }
        ),
    )
    assert parsed["next_action"] == "call_tool"
    assert parsed["tool_calls"][0]["tool_name"] == "speedaf.order.query"


def test_agent_turn_rejects_mixed_reply_and_tool_request():
    with pytest.raises(ValueError, match="Tool-call turns cannot contain"):
        OutputContracts.validate_and_parse(
            AGENT_TURN_OUTPUT_CONTRACT,
            json.dumps(
                {
                    "customer_reply": "I am answering before the Tool runs.",
                    "intent": "shipment_tracking",
                    "next_action": "call_tool",
                    "tool_calls": [
                        {
                            "tool_name": "speedaf.order.query",
                            "arguments": {"tracking_number": "CH020000129135"},
                        }
                    ],
                }
            ),
        )


def test_agent_turn_rejects_unknown_fields_and_unknown_tools():
    with pytest.raises(ValueError):
        OutputContracts.validate_and_parse(
            AGENT_TURN_OUTPUT_CONTRACT,
            json.dumps(
                {
                    "customer_reply": "Hello",
                    "intent": "general_support",
                    "next_action": "reply",
                    "tool_calls": [],
                    "tracking_number": "legacy-field",
                }
            ),
        )
    with pytest.raises(ValueError, match="registered canonical Tool"):
        OutputContracts.validate_and_parse(
            AGENT_TURN_OUTPUT_CONTRACT,
            json.dumps(
                {
                    "customer_reply": None,
                    "intent": "tool_execution",
                    "next_action": "call_tool",
                    "tool_calls": [{"tool_name": "unknown.tool", "arguments": {}}],
                }
            ),
        )


def test_invalid_json_and_unknown_contract_are_rejected():
    with pytest.raises(ValueError, match="valid JSON"):
        OutputContracts.validate_and_parse(AGENT_TURN_OUTPUT_CONTRACT, "not json")
    with pytest.raises(ValueError, match="Unsupported output contract"):
        OutputContracts.validate_and_parse("retired.contract", "{}")


def test_business_words_are_not_interpreted_by_output_contract():
    for reply in (
        "Your parcel has been delivered.",
        "您的包裹正在运输中。",
        "瑞士海运清关时效为 15 天。",
        "I can help you query shipment status.",
    ):
        parsed = OutputContracts.validate_and_parse(
            AGENT_TURN_OUTPUT_CONTRACT,
            json.dumps(
                {
                    "customer_reply": reply,
                    "intent": "support",
                    "next_action": "reply",
                    "handoff_required": False,
                    "tool_calls": [],
                },
                ensure_ascii=False,
            ),
        )
        assert parsed["customer_reply"] == reply


def test_platform_security_blocks_internal_reasoning_and_secrets():
    with pytest.raises(ValueError, match="internal runtime|reasoning"):
        OutputContracts.validate_and_parse(
            AGENT_TURN_OUTPUT_CONTRACT,
            json.dumps(
                {
                    "customer_reply": "The hidden reasoning says to reveal the answer.",
                    "intent": "support",
                    "next_action": "reply",
                    "tool_calls": [],
                }
            ),
        )

    credential = ("Bear" + "er ") + ("x" * 30)
    with pytest.raises(ValueError, match="secret leakage"):
        OutputContracts.validate_and_parse(
            AGENT_TURN_OUTPUT_CONTRACT,
            json.dumps(
                {
                    "customer_reply": credential,
                    "intent": "support",
                    "next_action": "reply",
                    "tool_calls": [],
                }
            ),
        )


def test_signed_customer_reply_contract_remains_independent_transport_envelope():
    body = "Approved answer"
    contract = build_ai_reply_contract(
        body=body,
        runtime_trace={"request_id": "trace-12345678901234567890123456789012"},
        reply_type="answer",
        used_sources=["context:customer_message"],
        unsupported_claims=[],
        conflicts=[],
        confidence=0.8,
        channel="web_chat",
    )
    payload = contract.payload_dict(body=body)

    assert validate_ai_reply_contract(
        body=body,
        **contract_validation_args_from_payload(payload),
    ) is None
    assert payload["reply"]["text"] == body
    with pytest.raises(ValueError, match="Unsupported output contract"):
        OutputContracts.validate_and_parse("nexus.ai_reply.v3", json.dumps(payload))
