from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


class FastReplyParseError(ValueError):
    """Raised when OpenClaw output cannot be accepted as customer reply JSON."""

    error_code = "ai_invalid_output"


class UnexpectedToolCallError(FastReplyParseError):
    """Raised when the webchat fast agent attempts to return a tool/function call."""

    error_code = "ai_unexpected_tool_call"


_ALLOWED_INTENTS = {
    "greeting",
    "tracking",
    "tracking_missing_number",
    "tracking_unresolved",
    "complaint",
    "address_change",
    "handoff",
    "other",
}
_REQUIRED_KEYS = {
    "reply",
    "intent",
    "tracking_number",
    "handoff_required",
    "handoff_reason",
    "recommended_agent_action",
}
_INTERNAL_PATTERNS = [
    r"\bOpenClaw\b",
    r"\bgateway\b",
    r"\bprompt\b",
    r"\bsystem prompt\b",
    r"\bdeveloper message\b",
    r"\btoken\b",
    r"\blocalhost\b",
    r"\b127\.0\.0\.1\b",
    r"\bport\s*\d+\b",
]


@dataclass(frozen=True)
class ParsedFastReply:
    reply: str
    intent: str
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None


def _content_text_from_block(block: Any) -> str | None:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return None
    block_type = str(block.get("type") or "").lower()
    if "function" in block_type or "tool" in block_type:
        raise UnexpectedToolCallError("OpenClaw returned a tool/function call in webchat fast reply output")
    for key in ("text", "output_text", "content"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value
    nested = block.get("text")
    if isinstance(nested, dict):
        value = nested.get("value")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_response_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        raise FastReplyParseError("OpenClaw response must be a JSON object or text body")

    for direct_key in ("output_text", "replyText", "text"):
        value = payload.get(direct_key)
        if isinstance(value, str) and value.strip():
            return value

    response = payload.get("response")
    if isinstance(response, dict):
        for nested_key in ("output_text", "text"):
            value = response.get(nested_key)
            if isinstance(value, str) and value.strip():
                return value

    output = payload.get("output")
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if "function" in item_type or "tool" in item_type:
                raise UnexpectedToolCallError("OpenClaw returned a tool/function call in webchat fast reply output")
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    text = _content_text_from_block(block)
                    if text:
                        texts.append(text)
            else:
                text = _content_text_from_block(item)
                if text:
                    texts.append(text)
        if texts:
            return "\n".join(texts).strip()

    choices = payload.get("choices")
    if isinstance(choices, list):
        texts = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if isinstance(message, dict):
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    raise UnexpectedToolCallError("OpenClaw returned tool calls in webchat fast reply output")
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    texts.append(content)
        if texts:
            return "\n".join(texts).strip()

    raise FastReplyParseError("OpenClaw response did not contain a text output")


def _parse_pure_json_text(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        raise FastReplyParseError("AI output is empty")
    if cleaned.startswith("```") or cleaned.endswith("```"):
        raise FastReplyParseError("AI output must be pure JSON, not fenced markdown")
    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        raise FastReplyParseError("AI output must be pure JSON with no surrounding prose")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise FastReplyParseError(f"AI output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FastReplyParseError("AI output JSON must be an object")
    return parsed


def _clean_optional_string(value: Any, *, max_chars: int = 500) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = " ".join(value.strip().split())
    return cleaned[:max_chars] if cleaned else None


def _sanitize_reply(reply: str) -> str:
    cleaned = re.sub(r"[ \t]+", " ", reply.strip())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    for pattern in _INTERNAL_PATTERNS:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            raise FastReplyParseError("AI reply contains internal system or gateway terms")
    return cleaned[:1200]


def parse_openclaw_fast_reply_from_strict_json(payload: dict[str, Any]) -> ParsedFastReply:
    """Validate a strict Fast Lane JSON object.

    Accepted shape:
    {
      "reply": str,
      "intent": str,
      "tracking_number": str | null,
      "handoff_required": bool,
      "handoff_reason": str | null,
      "recommended_agent_action": str | null,
    }
    """

    parsed = dict(payload)
    missing = sorted(_REQUIRED_KEYS - set(parsed.keys()))
    if missing:
        raise FastReplyParseError(f"AI output missing required keys: {', '.join(missing)}")

    reply_raw = parsed.get("reply")
    if not isinstance(reply_raw, str) or not reply_raw.strip():
        raise FastReplyParseError("AI output reply must be a non-empty string")
    reply = _sanitize_reply(reply_raw)
    if not reply:
        raise FastReplyParseError("AI output reply became empty after sanitization")

    intent = _clean_optional_string(parsed.get("intent"), max_chars=80) or "other"
    if intent not in _ALLOWED_INTENTS:
        intent = "other"

    handoff_required = parsed.get("handoff_required")
    if not isinstance(handoff_required, bool):
        raise FastReplyParseError("AI output handoff_required must be boolean")

    tracking_number = _clean_optional_string(parsed.get("tracking_number"), max_chars=120)
    handoff_reason = _clean_optional_string(parsed.get("handoff_reason"), max_chars=240)
    recommended_agent_action = _clean_optional_string(parsed.get("recommended_agent_action"), max_chars=500)

    return ParsedFastReply(
        reply=reply,
        intent=intent,
        tracking_number=tracking_number,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        recommended_agent_action=recommended_agent_action,
    )


def parse_openclaw_fast_reply(payload: Any) -> ParsedFastReply:
    """Parse and validate WebChat Fast Lane AI output.

    Input contract:
    - strict Fast Lane JSON object (already json.loads'ed dict), or
    - strict JSON text body, or
    - an OpenClaw envelope carrying strict JSON in output_text/text/response.output_text.

    Only strict pure JSON output is accepted. Tool/function calls, markdown
    fenced JSON, mixed prose, missing keys, and internal system terms are
    rejected.
    """

    if isinstance(payload, dict) and _REQUIRED_KEYS.issubset(payload.keys()):
        return parse_openclaw_fast_reply_from_strict_json(payload)

    raw_text = _extract_response_text(payload)
    parsed = _parse_pure_json_text(raw_text)
    return parse_openclaw_fast_reply_from_strict_json(parsed)
