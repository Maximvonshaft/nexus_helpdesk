"""Deterministic Webchat Fast Lane business-policy replies."""

from __future__ import annotations

import re
from typing import Any


SUPPORT_HOURS_REPLY = (
    "AI parcel support is available 24/7. "
    "Human support is available Monday-Friday, 09:00-17:00. "
    "Send your tracking number and I can help check your parcel immediately."
)

_SUPPORT_TERMS = (
    "support",
    "customer service",
    "service team",
    "agent",
    "human",
    "teammate",
    "parcel support",
    "parcel delivery support",
    "客服",
    "人工",
    "客服时间",
    "服务时间",
    "营业时间",
    "kundendienst",
    "supportzeiten",
    "öffnungszeiten",
    "oeffnungszeiten",
)

_HOURS_TERMS = (
    "hour",
    "hours",
    "time",
    "times",
    "available",
    "availability",
    "open",
    "opening",
    "business hours",
    "working hours",
    "office hours",
    "when are you",
    "when can",
    "什么时候",
    "几点",
    "时间",
    "营业",
    "上班",
    "verfügbar",
    "verfuegbar",
    "zeiten",
)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()[:1000]


def is_support_hours_question(body: Any) -> bool:
    text = _normalize_text(body)
    if not text:
        return False

    compact = re.sub(r"\s+", " ", text)

    has_support = any(term in compact for term in _SUPPORT_TERMS)
    has_hours = any(term in compact for term in _HOURS_TERMS)

    if has_support and has_hours:
        return True

    direct_patterns = (
        r"\bwhat\s+are\s+your\s+(customer\s+service|support)\s+hours\b",
        r"\bwhen\s+is\s+(customer\s+service|support)\s+available\b",
        r"\bare\s+you\s+available\s+24/7\b",
        r"\b24/7\s+(support|customer\s+service)\b",
        r"客服.*(时间|几点|营业|上班)",
        r"(时间|几点|营业|上班).*客服",
        r"(kundendienst|support).*zeiten",
        r"(öffnungszeiten|oeffnungszeiten).*(kundendienst|support)",
    )

    return any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in direct_patterns)


def match_support_hours_policy_reply(body: Any) -> dict[str, Any] | None:
    if not is_support_hours_question(body):
        return None

    return {
        "ok": True,
        "ai_generated": False,
        "reply_source": "server_support_hours_policy",
        "reply": SUPPORT_HOURS_REPLY,
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "ticket_creation_queued": False,
        "elapsed_ms": 0,
        "error_code": None,
        "retry_after_ms": None,
    }
