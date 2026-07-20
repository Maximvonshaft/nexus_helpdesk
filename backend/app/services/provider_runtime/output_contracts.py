from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

from ..webchat_ai_decision_runtime.schemas import AIDecision

AGENT_TURN_OUTPUT_CONTRACT = "nexus.agent_turn.v1"
WEBCHAT_RUNTIME_OUTPUT_CONTRACT = AGENT_TURN_OUTPUT_CONTRACT
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
)
_INTERNAL_MARKERS = (
    "<think",
    "hidden reasoning",
    "chain of thought",
    "developer message",
    "developer instruction",
    "system prompt",
    "provider_runtime",
    "localhost",
    "127.0.0.1",
)


class OutputContracts:
    @staticmethod
    def get_schema(contract_name: str) -> dict[str, Any]:
        if contract_name == AGENT_TURN_OUTPUT_CONTRACT:
            return AIDecision.model_json_schema()
        if contract_name == "nexus.ai_reply.v3":
            return {
                "type": "object",
                "properties": {
                    "reply": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["answer", "clarifying_question", "handoff_notice", "null_reply"],
                            },
                            "text": {"type": ["string", "null"], "maxLength": 4000},
                        },
                        "required": ["type", "text"],
                        "additionalProperties": False,
                    },
                    "language": {"type": "string", "maxLength": 32},
                    "intent": {"type": "string", "maxLength": 80},
                    "tracking_number": {"type": ["string", "null"], "maxLength": 80},
                    "handoff_required": {"type": "boolean"},
                    "handoff_reason": {"type": ["string", "null"], "maxLength": 500},
                    "recommended_agent_action": {"type": ["string", "null"], "maxLength": 500},
                    "ticket_should_create": {"type": "boolean"},
                    "internal_summary": {"type": ["string", "null"], "maxLength": 1000},
                    "risk_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "runtime_trace_id": {"type": "string", "maxLength": 120},
                    "contract_version": {"type": "string", "const": "nexus.ai_reply.v3"},
                    "runtime_signature": {"type": "string", "minLength": 32, "maxLength": 128},
                    "safety_status": {"type": "string", "enum": ["passed", "reviewed"]},
                    "origin": {"type": "string", "enum": ["provider_runtime", "ai_runtime"]},
                    "customer_visible": {"type": "boolean"},
                    "grounding": {
                        "type": "object",
                        "properties": {
                            "used_sources": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                            "unsupported_claims": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                            "conflicts": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        },
                        "required": ["used_sources", "unsupported_claims", "conflicts"],
                        "additionalProperties": False,
                    },
                    "risk": {
                        "type": "object",
                        "properties": {"confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                        "required": ["confidence"],
                        "additionalProperties": False,
                    },
                    "channel": {"type": "string", "maxLength": 80},
                },
                "required": [
                    "reply",
                    "language",
                    "intent",
                    "handoff_required",
                    "ticket_should_create",
                    "runtime_trace_id",
                    "contract_version",
                    "runtime_signature",
                    "safety_status",
                    "origin",
                    "customer_visible",
                    "grounding",
                    "risk",
                    "channel",
                ],
                "additionalProperties": False,
            }
        return {}

    @staticmethod
    def validate_and_parse(contract_name: str, raw_output: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError("Output must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Output must be a JSON object")

        if contract_name == AGENT_TURN_OUTPUT_CONTRACT:
            decision = AIDecision.model_validate(parsed)
            if decision.customer_reply:
                OutputContracts.check_customer_visible_security(decision.customer_reply)
            return decision.model_dump(exclude_none=True)

        schema = OutputContracts.get_schema(contract_name)
        if not schema:
            raise ValueError("Unsupported output contract")
        try:
            jsonschema.validate(instance=parsed, schema=schema)
        except jsonschema.exceptions.ValidationError as exc:
            raise ValueError(f"Schema validation failed: {exc.message}") from exc
        if contract_name == "nexus.ai_reply.v3":
            OutputContracts._validate_ai_reply(parsed)
        return parsed

    @staticmethod
    def check_customer_visible_security(reply: str) -> None:
        lowered = reply.lower()
        if any(marker.lower() in lowered for marker in _INTERNAL_MARKERS):
            raise ValueError("Customer reply contains internal runtime or reasoning content")
        if any(pattern.search(reply) for pattern in _SECRET_PATTERNS):
            raise ValueError("Potential secret leakage detected")

    @staticmethod
    def _validate_ai_reply(parsed: dict[str, Any]) -> None:
        reply = parsed.get("reply") if isinstance(parsed.get("reply"), dict) else {}
        grounding = parsed.get("grounding") if isinstance(parsed.get("grounding"), dict) else {}
        if reply.get("type") == "null_reply":
            if parsed.get("customer_visible") is not False or reply.get("text") is not None:
                raise ValueError("null_reply requires customer_visible=false and reply.text=null")
            return
        if reply.get("type") == "answer" and not grounding.get("used_sources"):
            raise ValueError("answer requires grounding.used_sources")
        if reply.get("type") == "answer" and grounding.get("unsupported_claims"):
            raise ValueError("answer cannot contain unsupported_claims")
