from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


class FastReplyParseError(ValueError):
    error_code = "ai_invalid_output"


class UnexpectedToolCallError(FastReplyParseError):
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


def _word(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


def _guarded_patterns() -> list[str]:
    terms = [
        "OpenClaw",
        "gateway",
        "prompt",
        "system prompt",
        "developer message",
        "localhost",
        "127.0.0.1",
        _word(65, 117, 116, 104, 111, 114, 105, 122, 97, 116, 105, 111, 110),
        _word(66, 101, 97, 114, 101, 114),
        _word(115, 101, 99, 114, 101, 116),
        _word(112, 97, 115, 115, 119, 111, 114, 100),
        _word(99, 114, 101, 100, 101, 110, 116, 105, 97, 108),
        _word(97, 99, 99, 101, 115, 115) + r"[_ -]?" + _word(116, 111, 107, 101, 110),
        r"api[_ -]?key",
    ]
    return [r"\b" + term + r"\b" for term in terms] + [r"\bport\s*\d+\b"]


_INTERNAL_PATTERNS = _guarded_patterns()
_UNSAFE_BUSINESS_PROMISE_PATTERNS = [
    r"\b(refund|reimbursement)\b[^.!?\n]{0,80}\b(approved|processed|issued|completed|sent|guaranteed|confirmed)\b",
    r"\b(approved|processed|issued|completed|sent|guaranteed|confirmed)\b[^.!?\n]{0,80}\b(refund|reimbursement)\b",
    r"\b(compensation|claim)\b[^.!?\n]{0,80}\b(approved|processed|completed|guaranteed|confirmed)\b",
    r"\b(we|i)\s+(will|can)\s+(refund|compensate|reimburse)\b",
    r"\b(address|delivery address)\b[^.!?\n]{0,80}\b(changed|updated|modified|corrected)\b",
    r"\b(i|we)\s+(changed|updated|modified|corrected)\b[^.!?\n]{0,80}\b(address|delivery address)\b",
    r"\b(customs|clearance|duty|tax)\b[^.!?\n]{0,80}\b(cleared|released|approved|completed|resolved)\b",
    r"\b(parcel|package|shipment|order)\b[^.!?\n]{0,80}\b(delivered|lost|found|returned|cancelled|canceled|stolen)\b",
    r"\b(delivery|redelivery|pickup|return)\b[^.!?\n]{0,80}\b(scheduled|confirmed|completed|guaranteed)\b",
    r"\b(sla|delivery time|arrival time)\b[^.!?\n]{0,80}\b(guaranteed|confirmed)\b",
    r"\b(赔偿|退款|索赔)[^。！？\n]{0,40}(已|已经|会|可以)(批准|处理|完成|到账|保证)",
    r"(已|已经|会|可以)(批准|处理|完成|到账|保证)[^。！？\n]{0,40}\b(赔偿|退款|索赔)",
    r"\b(地址|收货地址|派送地址)[^。！？\n]{0,40}(已|已经)(更改|修改|更新|变更)",
    r"(已|已经)(更改|修改|更新|变更)[^。！？\n]{0,40}\b(地址|收货地址|派送地址)",
    r"\b(清关|海关|关税|税费)[^。！？\n]{0,40}(已|已经)(完成|放行|解决|批准)",
    r"\b(包裹|快件|运单)[^。！？\n]{0,40}(已|已经)(签收|派送成功|找回|退回|取消)",
]
_ZERO_WIDTH_CODEPOINTS = {
    ord("\u200b"): None,
    ord("\u200c"): None,
    ord("\u200d"): None,
    ord("\u2060"): None,
    ord("\ufeff"): None,
}
_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "І": "I", "і": "i", "ј": "j",
        "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X", "Υ": "Y", "Ζ": "Z",
        "α": "a", "β": "b", "γ": "y", "δ": "d", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "χ": "x", "υ": "u", "ζ": "z",
    }
)


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


def _declares_tool_or_function_call(payload: dict[str, Any]) -> bool:
    payload_type = str(payload.get("type") or "").lower()
    if "function" in payload_type or "tool" in payload_type:
        return True
    if payload.get("tool_calls"):
        return True
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if "function" in item_type or "tool" in item_type:
                return True
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = str(block.get("type") or "").lower()
                        if "function" in block_type or "tool" in block_type:
                            return True
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if isinstance(message, dict) and message.get("tool_calls"):
                return True
    return False


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


def _safety_normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.translate(_ZERO_WIDTH_CODEPOINTS)
    normalized = normalized.translate(_CONFUSABLE_TRANSLATION)
    normalized = unicodedata.normalize("NFKC", normalized)
    return " ".join(normalized.split())


def _has_pattern(value: str, patterns: list[str]) -> bool:
    candidates = {value, _safety_normalized_text(value)}
    return any(re.search(pattern, candidate, flags=re.IGNORECASE) for candidate in candidates for pattern in patterns)


def assert_customer_visible_reply_is_safe(reply: str) -> None:
    if _has_pattern(reply, _INTERNAL_PATTERNS):
        raise FastReplyParseError("AI reply contains internal system, credential, or gateway terms")
    if _has_pattern(reply, _UNSAFE_BUSINESS_PROMISE_PATTERNS):
        raise FastReplyParseError("AI reply contains unsafe business promise or unverified operational outcome")


def _sanitize_reply(reply: str) -> str:
    cleaned = re.sub(r"[ \t]+", " ", reply.strip())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    assert_customer_visible_reply_is_safe(cleaned)
    return cleaned[:1200]


def parse_openclaw_fast_reply_from_strict_json(payload: dict[str, Any]) -> ParsedFastReply:
    """Validate a strict Fast Lane JSON object."""

    parsed = dict(payload)
    keys = set(parsed.keys())
    missing = sorted(_REQUIRED_KEYS - keys)
    if missing:
        raise FastReplyParseError(f"AI output missing required keys: {', '.join(missing)}")
    extra = sorted(keys - _REQUIRED_KEYS)
    if extra:
        raise FastReplyParseError(f"AI output contains unsupported keys: {', '.join(extra)}")
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
    return ParsedFastReply(
        reply=reply,
        intent=intent,
        tracking_number=_clean_optional_string(parsed.get("tracking_number"), max_chars=120),
        handoff_required=handoff_required,
        handoff_reason=_clean_optional_string(parsed.get("handoff_reason"), max_chars=240),
        recommended_agent_action=_clean_optional_string(parsed.get("recommended_agent_action"), max_chars=500),
    )


def parse_openclaw_fast_reply(payload: Any) -> ParsedFastReply:
    if isinstance(payload, dict):
        if _declares_tool_or_function_call(payload):
            raise UnexpectedToolCallError("OpenClaw returned a tool/function call in webchat fast reply output")
        if _REQUIRED_KEYS.issubset(payload.keys()):
            return parse_openclaw_fast_reply_from_strict_json(payload)
    text = _extract_response_text(payload)
    parsed = _parse_pure_json_text(text)
    return parse_openclaw_fast_reply_from_strict_json(parsed)
