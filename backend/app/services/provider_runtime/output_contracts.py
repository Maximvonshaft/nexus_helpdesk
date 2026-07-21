from __future__ import annotations

import json
import re
from typing import Any

from ..agent_runtime.specialist_schemas import SpecialistResult
from ..webchat_ai_decision_runtime.schemas import AIDecision

AGENT_TURN_OUTPUT_CONTRACT = "nexus.agent_turn.v1"
AGENT_SPECIALIST_OUTPUT_CONTRACT = "nexus.agent_specialist.v1"
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
        if contract_name == AGENT_SPECIALIST_OUTPUT_CONTRACT:
            return SpecialistResult.model_json_schema()
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
                OutputContracts.check_customer_visible_security(
                    decision.customer_reply
                )
            return decision.model_dump(exclude_none=True)
        if contract_name == AGENT_SPECIALIST_OUTPUT_CONTRACT:
            result = SpecialistResult.model_validate(parsed)
            rendered = json.dumps(
                result.model_dump(exclude_none=True),
                ensure_ascii=False,
            )
            OutputContracts.check_internal_evidence_security(rendered)
            return result.model_dump(exclude_none=True)
        raise ValueError("Unsupported output contract")

    @staticmethod
    def check_customer_visible_security(reply: str) -> None:
        lowered = reply.lower()
        if any(marker.lower() in lowered for marker in _INTERNAL_MARKERS):
            raise ValueError(
                "Customer reply contains internal runtime or reasoning content"
            )
        if any(pattern.search(reply) for pattern in _SECRET_PATTERNS):
            raise ValueError("Potential secret leakage detected")

    @staticmethod
    def check_internal_evidence_security(value: str) -> None:
        lowered = value.lower()
        if any(
            marker.lower() in lowered
            for marker in ("<think", "chain of thought", "hidden reasoning")
        ):
            raise ValueError("Specialist output contains hidden reasoning content")
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise ValueError("Potential secret leakage detected")
