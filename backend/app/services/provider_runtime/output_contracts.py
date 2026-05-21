from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

_SECRET_PATTERNS = [
    re.compile(("s" + "k-") + r"[A-Za-z0-9_\-]{12,}"),
    re.compile(("ey" + "J") + r"[A-Za-z0-9_\-]{12,}"),
    re.compile(("Bear" + "er") + r"\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
]
_INTERNAL_PATTERNS = ["localhost", "127.0.0.1", "::1", "bridge", "codex_app_server", "openclaw", "provider_runtime"]
_STATUS_WORDS = [
    "delivered", "in transit", "out for delivery", "customs", "returned", "failed delivery",
    "派送", "已签收", "运输中", "清关", "退回",
]


class OutputContracts:
    @staticmethod
    def get_schema(contract_name: str) -> dict[str, Any]:
        if contract_name == "speedaf_webchat_fast_reply_v1":
            return {
                "type": "object",
                "properties": {
                    "customer_reply": {"type": "string", "maxLength": 1200},
                    "language": {"type": "string", "maxLength": 32},
                    "intent": {"type": "string", "enum": ["greeting", "tracking", "tracking_missing_number", "tracking_unresolved", "complaint", "address_change", "handoff", "other"]},
                    "tracking_number": {"type": ["string", "null"], "maxLength": 80},
                    "handoff_required": {"type": "boolean"},
                    "handoff_reason": {"type": ["string", "null"], "maxLength": 500},
                    "recommended_agent_action": {"type": ["string", "null"], "maxLength": 500},
                    "ticket_should_create": {"type": "boolean"},
                    "internal_summary": {"type": ["string", "null"], "maxLength": 1000},
                    "risk_flags": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                },
                "required": ["customer_reply", "language", "intent", "handoff_required", "ticket_should_create"],
                "additionalProperties": False,
            }
        if contract_name == "speedaf_ticket_triage_v1":
            return {
                "type": "object",
                "properties": {
                    "ticket_title": {"type": "string", "maxLength": 200},
                    "ticket_category": {"type": "string", "enum": ["delivery_exception", "tracking", "complaint", "address_change", "claim", "other"]},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "customer_reply": {"type": "string", "maxLength": 1200},
                    "agent_brief": {"type": "string", "maxLength": 2000},
                    "required_human_action": {"type": ["string", "null"], "maxLength": 1000},
                    "evidence_needed": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                    "handoff_required": {"type": "boolean"},
                },
                "required": ["ticket_title", "ticket_category", "priority", "customer_reply", "agent_brief", "handoff_required", "evidence_needed"],
                "additionalProperties": False,
            }
        if contract_name == "speedaf_delivery_exception_analysis_v1":
            return {
                "type": "object",
                "properties": {
                    "exception_type": {"type": "string", "enum": ["failed_delivery", "delivered_not_received", "wrong_address", "customs", "damaged", "lost", "other"]},
                    "root_cause_guess": {"type": ["string", "null"], "maxLength": 1000},
                    "next_action": {"type": "string", "enum": ["reattempt", "investigate", "return", "manual_review", "none"]},
                    "customer_visible_reply": {"type": "string", "maxLength": 1200},
                    "internal_action_required": {"type": "boolean"},
                    "evidence_needed": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                },
                "required": ["exception_type", "next_action", "customer_visible_reply", "internal_action_required", "evidence_needed"],
                "additionalProperties": False,
            }
        return {}

    @staticmethod
    def validate_and_parse(contract_name: str, raw_output: str, evidence_present: bool = False) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError("Output must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Output must be a JSON object")

        schema = OutputContracts.get_schema(contract_name)
        if schema:
            try:
                jsonschema.validate(instance=parsed, schema=schema)
            except jsonschema.exceptions.ValidationError as exc:
                raise ValueError(f"Schema validation failed: {exc.message}") from exc

        OutputContracts.check_security_rules(raw_output=raw_output, parsed=parsed, evidence_present=evidence_present)
        return parsed

    @staticmethod
    def check_security_rules(*, raw_output: str, parsed: dict[str, Any], evidence_present: bool = False) -> None:
        lower_raw = raw_output.lower()
        if "```" in raw_output or "~~~" in raw_output:
            raise ValueError("Markdown code blocks are prohibited")
        if "<think" in lower_raw or "hidden reasoning" in lower_raw:
            raise ValueError("Hidden reasoning is prohibited")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(raw_output):
                raise ValueError("Potential secret leakage detected")
        for marker in _INTERNAL_PATTERNS:
            if marker in lower_raw:
                raise ValueError("Internal runtime references are prohibited")

        intent = parsed.get("intent")
        reply = str(parsed.get("customer_reply") or parsed.get("customer_visible_reply") or "")
        reply_lower = reply.lower()
        if intent == "tracking":
            if not parsed.get("tracking_number"):
                raise ValueError("Tracking intent without a tracking_number is prohibited")
            if not evidence_present:
                raise ValueError("Tracking status output requires trusted tracking evidence")
        if not evidence_present and any(word in reply_lower for word in _STATUS_WORDS):
            raise ValueError("Parcel status language requires trusted tracking evidence")
