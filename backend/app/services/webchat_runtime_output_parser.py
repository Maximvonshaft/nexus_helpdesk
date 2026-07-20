from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


class RuntimeReplyParseError(ValueError):
    """Raised when AI Runtime output cannot be accepted as customer reply JSON."""

    error_code = "ai_invalid_output"


class UnexpectedRuntimeToolCallError(RuntimeReplyParseError):
    """Raised when a model emits provider-native tool calls instead of the decision JSON contract."""

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
    # New AI decision runtime intents. Keep legacy values above for backward compatibility.
    "unclear",
    "handoff_request",
    "refusal_request",
    "general_support",
}
_REQUIRED_KEYS = {
    "reply",
    "intent",
    "tracking_number",
    "handoff_required",
    "handoff_reason",
    "recommended_agent_action",
}
_DECISION_KEYS = {
    "customer_reply",
    "intent",
    "confidence",
    "risk_level",
    "next_action",
    "handoff_required",
    "handoff_reason",
    "tool_calls",
    "evidence_used",
    "safety_notes",
}
_INTERNAL_PATTERNS = [
    r"\bgateway\b",
    r"\bprompt\b",
    r"\bsystem prompt\b",
    r"\bdeveloper message\b",
    r"\blocalhost\b",
    r"\b127\.0\.0\.1\b",
    r"\bport\s*\d+\b",
    r"\bAuthorization\b",
    r"\bBearer\b",
    r"\bapi[_ -]?key\b",
    r"\bsecret\b",
    r"\bpassword\b",
    r"\bcredential\b",
    r"\baccess[_ -]?token\b",
    r"没有可信的追踪证据之前",
    r"没有可信追踪证据之前",
    r"不要尝试提供",
    r"不得尝试提供",
    r"不得判断",
]
_UNSAFE_BUSINESS_PROMISE_PATTERNS = [
    r"\b(refund|reimbursement)\b[^.!?\n]{0,80}\b(approved|processed|issued|completed|sent|guaranteed|confirmed)\b",
    r"\b(approved|processed|issued|completed|sent|guaranteed|confirmed)\b[^.!?\n]{0,80}\b(refund|reimbursement)\b",
    r"\b(compensation|claim)\b[^.!?\n]{0,80}\b(approved|processed|completed|guaranteed|confirmed)\b",
    r"\b(we|i)\s+(will|can)\s+(refund|compensate|reimburse)\b",
    r"\b(address|delivery address)\b[^.!?\n]{0,80}\b(changed|updated|modified|corrected)\b",
    r"\b(i|we)\s+(changed|updated|modified|corrected)\b[^.!?\n]{0,80}\b(address|delivery address)\b",
    r"\b(customs|clearance|duty|tax)\b[^.!?\n]{0,80}\b(cleared|released|approved|completed|resolved)\b",
    r"\b(delivery|redelivery|pickup|return)\b[^.!?\n]{0,80}\b(scheduled|confirmed|completed|guaranteed)\b",
    r"\b(sla|delivery time|arrival time)\b[^.!?\n]{0,80}\b(guaranteed|confirmed)\b",
    r"\b(赔偿|退款|索赔)[^。！？\n]{0,40}(已|已经|会|可以)(批准|处理|完成|到账|保证)",
    r"(已|已经|会|可以)(批准|处理|完成|到账|保证)[^。！？\n]{0,40}\b(赔偿|退款|索赔)",
    r"\b(地址|收货地址|派送地址)[^。！？\n]{0,40}(已|已经)(更改|修改|更新|变更)",
    r"(已|已经)(更改|修改|更新|变更)[^。！？\n]{0,40}\b(地址|收货地址|派送地址)",
    r"\b(清关|海关|关税|税费)[^。！？\n]{0,40}(已|已经)(完成|放行|解决|批准)",
]
_UNVERIFIED_SHIPMENT_OUTCOME_PATTERNS = [
    r"\b(parcel|package|shipment|order)\b[^.!?\n]{0,80}\b(delivered|lost|found|returned|cancelled|canceled|stolen)\b",
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


# Provider-native tool/function calls are still forbidden at this boundary. The
# new WebChat AI decision runtime accepts tool *proposals* only inside strict JSON
# as [{"tool_name": ...}], which are then validated by Policy Gate.
def _looks_like_decision_json(payload: dict[str, Any]) -> bool:
    if "customer_reply" in payload:
        return True
    return _DECISION_KEYS.issubset(set(payload.keys())) or _REQUIRED_KEYS.issubset(set(payload.keys()))


def _looks_like_runtime_tool_proposals(value: Any) -> bool:
    if value in (None, []):
        return True
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not (item.get("tool_name") or item.get("name") or item.get("tool")):
            return False
    return True


def _content_text_from_block(block: Any) -> str | None:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return None
    block_type = str(block.get("type") or "").lower()
    if "function" in block_type or "tool" in block_type:
        raise UnexpectedRuntimeToolCallError("AI provider returned a provider-native tool/function call in webchat runtime output")
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


def _declares_provider_tool_or_function_call(payload: dict[str, Any]) -> bool:
    payload_type = str(payload.get("type") or "").lower()
    if "function" in payload_type or "tool" in payload_type:
        return True
    if payload.get("tool_calls") and not (_looks_like_decision_json(payload) and _looks_like_runtime_tool_proposals(payload.get("tool_calls"))):
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
        raise RuntimeReplyParseError("AI provider response must be a JSON object or text body")

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
                raise UnexpectedRuntimeToolCallError("AI provider returned a provider-native tool/function call in webchat runtime output")
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
                    raise UnexpectedRuntimeToolCallError("AI provider returned provider-native tool calls in webchat runtime output")
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    texts.append(content)
        if texts:
            return "\n".join(texts).strip()

    raise RuntimeReplyParseError("AI provider response did not contain a text output")


def _parse_pure_json_text(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        raise RuntimeReplyParseError("AI output is empty")
    if cleaned.startswith("```") or cleaned.endswith("```"):
        raise RuntimeReplyParseError("AI output must be pure JSON, not fenced markdown")
    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        raise RuntimeReplyParseError("AI output must be pure JSON with no surrounding prose")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeReplyParseError(f"AI output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeReplyParseError("AI output JSON must be an object")
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
    return any(
        re.search(pattern, candidate, flags=re.IGNORECASE)
        for candidate in candidates
        for pattern in patterns
    )


def assert_customer_visible_reply_is_safe(reply: str, *, evidence_present: bool = False) -> None:
    if _has_pattern(reply, _INTERNAL_PATTERNS):
        raise RuntimeReplyParseError("AI reply contains internal system, credential, or gateway terms")
    if _has_pattern(reply, _UNSAFE_BUSINESS_PROMISE_PATTERNS):
        raise RuntimeReplyParseError("AI reply contains unsafe business promise or unverified operational outcome")
    if not evidence_present and _has_pattern(reply, _UNVERIFIED_SHIPMENT_OUTCOME_PATTERNS):
        raise RuntimeReplyParseError("AI reply contains unverified shipment outcome")


def _sanitize_reply(reply: str, *, evidence_present: bool = False) -> str:
    cleaned = re.sub(r"[ \t]+", " ", reply.strip())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    cleaned = re.sub(r"waybill\s*号\s*运单尾号", "运单尾号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"waybill\s*号码", "运单号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"waybill\s*号", "运单号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"waybill", "运单", cleaned, flags=re.IGNORECASE)
    assert_customer_visible_reply_is_safe(cleaned, evidence_present=evidence_present)
    return cleaned[:1200]


def _clean_float(value: Any) -> float:
    try:
        cleaned = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, cleaned))


def _clean_tool_calls(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not _looks_like_runtime_tool_proposals(value):
        raise RuntimeReplyParseError("AI decision tool_calls must be runtime tool proposals with tool_name/name/tool")
    out: list[dict[str, Any]] = []
    for item in value[:12]:
        data = dict(item)
        tool_name = _clean_optional_string(data.get("tool_name") or data.get("name") or data.get("tool"), max_chars=160)
        if not tool_name:
            continue
        args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        out.append(
            {
                "tool_name": tool_name,
                "arguments": args,
                "idempotency_key": _clean_optional_string(data.get("idempotency_key"), max_chars=240),
                "reason": _clean_optional_string(data.get("reason"), max_chars=500),
                "requires_confirmation": data.get("requires_confirmation") if isinstance(data.get("requires_confirmation"), bool) else None,
            }
        )
    return out


def _clean_dict_list(value: Any, *, max_items: int) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:max_items] if isinstance(item, dict)]


def _clean_str_list(value: Any, *, max_items: int) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in raw[:max_items]:
        cleaned = _clean_optional_string(item, max_chars=300)
        if cleaned:
            out.append(cleaned)
    return out


def parse_runtime_reply_from_strict_json(payload: dict[str, Any], *, evidence_present: bool = False) -> ParsedRuntimeReply:
    """Validate strict WebChat Runtime JSON output.

    Accepted shapes:
    1. Legacy reply contract with reply/intent/tracking_number/handoff_required.
    2. AI decision runtime contract with customer_reply, next_action, tool_calls,
       evidence_used, and safety_notes.
    """

    parsed = dict(payload)
    if _declares_provider_tool_or_function_call(parsed):
        raise UnexpectedRuntimeToolCallError("AI provider returned a provider-native tool/function call in webchat runtime output")

    is_decision = "customer_reply" in parsed or "next_action" in parsed or "risk_level" in parsed
    if is_decision:
        missing = sorted({"customer_reply", "intent", "handoff_required"} - set(parsed.keys()))
    else:
        missing = sorted(_REQUIRED_KEYS - set(parsed.keys()))
    if missing:
        raise RuntimeReplyParseError(f"AI output missing required keys: {', '.join(missing)}")

    reply_raw = parsed.get("customer_reply") if is_decision else parsed.get("reply")
    if not isinstance(reply_raw, str) or not reply_raw.strip():
        raise RuntimeReplyParseError("AI output reply must be a non-empty string")
    reply = _sanitize_reply(reply_raw, evidence_present=evidence_present)
    if not reply:
        raise RuntimeReplyParseError("AI output reply became empty after sanitization")

    intent = _clean_optional_string(parsed.get("intent"), max_chars=80) or "other"
    if intent not in _ALLOWED_INTENTS:
        intent = "other"

    handoff_required = parsed.get("handoff_required")
    if not isinstance(handoff_required, bool):
        raise RuntimeReplyParseError("AI output handoff_required must be boolean")

    tracking_number = _clean_optional_string(parsed.get("tracking_number"), max_chars=120)
    handoff_reason = _clean_optional_string(parsed.get("handoff_reason"), max_chars=240)
    recommended_agent_action = _clean_optional_string(parsed.get("recommended_agent_action"), max_chars=500)
    tool_calls = _clean_tool_calls(parsed.get("tool_calls"))
    evidence_used = _clean_dict_list(parsed.get("evidence_used"), max_items=20)
    safety_notes = _clean_str_list(parsed.get("safety_notes"), max_items=20)
    confidence = _clean_float(parsed.get("confidence"))
    risk_level = _clean_optional_string(parsed.get("risk_level"), max_chars=20) or ("medium" if handoff_required else "low")
    next_action = _clean_optional_string(parsed.get("next_action"), max_chars=80) or ("request_handoff" if handoff_required else "reply")
    ai_decision = None
    if is_decision:
        ai_decision = {
            "customer_reply": reply,
            "intent": intent,
            "confidence": confidence,
            "risk_level": risk_level,
            "next_action": next_action,
            "handoff_required": handoff_required,
            "handoff_reason": handoff_reason,
            "tool_calls": tool_calls,
            "evidence_used": evidence_used,
            "safety_notes": safety_notes,
        }

    return ParsedRuntimeReply(
        reply=reply,
        intent=intent,
        tracking_number=tracking_number,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        recommended_agent_action=recommended_agent_action,
        confidence=confidence,
        risk_level=risk_level,
        next_action=next_action,
        tool_calls=tool_calls,
        evidence_used=evidence_used,
        safety_notes=safety_notes,
        ai_decision=ai_decision,
    )


def parse_runtime_reply_provider_output(payload: Any, *, evidence_present: bool = False) -> ParsedRuntimeReply:
    """Parse and validate WebChat Runtime AI output.

    The boundary is intentionally strict. Provider-native tool calls, markdown
    fences, surrounding prose, unsafe operational promises, and internal
    implementation terms are rejected. AI decision runtime tool proposals are
    accepted only as safe JSON fields and are still subject to Policy Gate.
    """

    if isinstance(payload, dict):
        if _looks_like_decision_json(payload):
            return parse_runtime_reply_from_strict_json(payload, evidence_present=evidence_present)
        if _declares_provider_tool_or_function_call(payload):
            raise UnexpectedRuntimeToolCallError("AI provider returned a provider-native tool/function call in webchat runtime output")

    text = _extract_response_text(payload)
    parsed = _parse_pure_json_text(text)
    return parse_runtime_reply_from_strict_json(parsed, evidence_present=evidence_present)
