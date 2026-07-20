from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .webchat_ai_decision_runtime.schemas import AIDecision


class RuntimeReplyParseError(ValueError):
    """Raised when provider output cannot be accepted as a canonical Agent turn."""

    error_code = "ai_invalid_output"


class UnexpectedRuntimeToolCallError(RuntimeReplyParseError):
    """Provider-native tool calls are not accepted; tools are JSON proposals."""

    error_code = "ai_unexpected_tool_call"


_ZERO_WIDTH_CODEPOINTS = {
    ord("\u200b"): None,
    ord("\u200c"): None,
    ord("\u200d"): None,
    ord("\u2060"): None,
    ord("\ufeff"): None,
}
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"\b(?:api[_ -]?key|secret[_ -]?key|access[_ -]?token|client[_ -]?secret|password|passwd)"
        r"\s*[:=]\s*[^\s,;]{8,}",
        re.IGNORECASE,
    ),
)
_INTERNAL_REASONING_MARKERS = (
    "<think",
    "</think",
    "chain of thought",
    "hidden reasoning",
    "developer instruction",
    "developer message",
    "system prompt",
)


@dataclass(frozen=True)
class ParsedRuntimeReply:
    reply: str
    intent: str
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    confidence: float = 0.0
    risk_level: str = "low"
    next_action: str = "reply"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    evidence_used: list[dict[str, Any]] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)
    ai_decision: dict[str, Any] | None = None


def assert_customer_visible_reply_is_safe(reply: str, *, evidence_present: bool = False) -> None:
    """Apply platform security only; never infer domain truth from customer text."""

    del evidence_present  # compatibility only; business evidence is handled by Skills and Tools.
    normalized = _safety_normalized_text(reply)
    lowered = normalized.lower()
    if any(marker in lowered for marker in _INTERNAL_REASONING_MARKERS):
        raise RuntimeReplyParseError("AI reply contains internal reasoning or instructions")
    if any(pattern.search(normalized) for pattern in _SECRET_PATTERNS):
        raise RuntimeReplyParseError("AI reply contains a credential or secret")


def parse_runtime_reply_from_strict_json(
    payload: dict[str, Any],
    *,
    evidence_present: bool = False,
) -> ParsedRuntimeReply:
    del evidence_present
    try:
        decision = AIDecision.model_validate(payload)
    except Exception as exc:
        raise RuntimeReplyParseError(f"AI Agent turn is invalid: {exc}") from exc
    if decision.next_action == "call_tool":
        raise RuntimeReplyParseError("Tool-call turns are not customer replies")
    reply = decision.customer_reply or ""
    assert_customer_visible_reply_is_safe(reply)
    data = decision.model_dump(exclude_none=True)
    return ParsedRuntimeReply(
        reply=reply,
        intent=decision.intent,
        tracking_number=None,
        handoff_required=decision.handoff_required,
        handoff_reason=decision.handoff_reason,
        recommended_agent_action=(
            "Review the conversation and take over." if decision.handoff_required else None
        ),
        confidence=decision.confidence,
        risk_level=decision.risk_level,
        next_action=decision.next_action,
        tool_calls=[item.model_dump(exclude_none=True) for item in decision.tool_calls],
        evidence_used=[item.model_dump(exclude_none=True) for item in decision.evidence_used],
        safety_notes=list(decision.safety_notes),
        ai_decision=data,
    )


def parse_runtime_reply_provider_output(
    payload: Any,
    *,
    evidence_present: bool = False,
) -> ParsedRuntimeReply:
    """Parse provider wrappers into one strict canonical Agent-turn JSON object."""

    if _declares_provider_native_tool_call(payload):
        raise UnexpectedRuntimeToolCallError(
            "AI provider returned a provider-native tool/function call"
        )
    parsed = _coerce_json_object(payload)
    return parse_runtime_reply_from_strict_json(parsed, evidence_present=evidence_present)


def _coerce_json_object(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if _looks_like_agent_turn(payload):
            return dict(payload)
        response = payload.get("response")
        if isinstance(response, dict):
            return _coerce_json_object(response)
        for key in ("output_text", "replyText", "text", "response_text", "answer", "raw_content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _parse_json_text(value)
        message = payload.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return _parse_json_text(message["content"])
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return _parse_json_text(message["content"])
    if isinstance(payload, str):
        return _parse_json_text(payload)
    raise RuntimeReplyParseError("AI provider response did not contain an Agent turn")


def _parse_json_text(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        raise RuntimeReplyParseError("AI output is empty")
    if text.startswith("```"):
        raise RuntimeReplyParseError("AI output must be pure JSON, not fenced markdown")
    if not (text.startswith("{") and text.endswith("}")):
        raise RuntimeReplyParseError("AI output must be pure JSON with no surrounding prose")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeReplyParseError(f"AI output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeReplyParseError("AI output JSON must be an object")
    return parsed


def _looks_like_agent_turn(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("customer_reply", "next_action", "tool_calls"))


def _declares_provider_native_tool_call(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("function_call"):
        return True
    if payload.get("tool_calls") and not _looks_like_agent_turn(payload):
        return True
    payload_type = str(payload.get("type") or "").lower()
    if "function" in payload_type or payload_type.startswith("tool_"):
        return True
    message = payload.get("message")
    if isinstance(message, dict) and message.get("tool_calls"):
        return True
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict) and message.get("tool_calls"):
                return True
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if "function" in item_type or item_type.startswith("tool_"):
                return True
    return False


def _safety_normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.translate(_ZERO_WIDTH_CODEPOINTS)
    return " ".join(normalized.split())
