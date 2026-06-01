from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LowSignalDecision:
    is_low_signal: bool
    reason: str | None = None


_ASCII_NOISE = {
    "?",
    "??",
    "???",
    "hi",
    "hello",
    "hey",
    "yo",
    "test",
    "testing",
    "asd",
    "asdasd",
    "qwe",
    "qwer",
    "321",
    "123",
}

_CJK_NOISE = {
    "你好",
    "您好",
    "哈喽",
    "嗨",
    "霓虹",
    "撒旦",
}

_BUSINESS_SIGNALS = (
    "track",
    "tracking",
    "waybill",
    "shipment",
    "parcel",
    "package",
    "delivery",
    "redelivery",
    "refuse",
    "refusal",
    "return",
    "address",
    "change address",
    "complaint",
    "claim",
    "refund",
    "compensation",
    "lost",
    "damage",
    "damaged",
    "delayed",
    "late",
    "customs",
    "pickup",
    "cod",
    "运单",
    "单号",
    "物流",
    "包裹",
    "快递",
    "派送",
    "配送",
    "重派",
    "重新派送",
    "拒收",
    "退回",
    "退货",
    "改地址",
    "地址",
    "投诉",
    "赔偿",
    "理赔",
    "退款",
    "丢件",
    "丢失",
    "破损",
    "延误",
    "清关",
    "取件",
)


def _text_from_context(recent_context: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for item in recent_context or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role in {"customer", "visitor", "user"}:
            value = str(item.get("text") or item.get("body") or "").strip()
            if value:
                parts.append(value[:500])
    return "\n".join(parts[-4:])


def classify_low_signal_customer_message(body: str | None, recent_context: list[dict[str, Any]] | None = None) -> LowSignalDecision:
    raw = str(body or "").strip()
    compact = "".join(raw.split())
    lowered = compact.lower()
    if not compact:
        return LowSignalDecision(True, "empty")

    combined = f"{raw}\n{_text_from_context(recent_context)}".lower()
    if any(signal in combined for signal in _BUSINESS_SIGNALS):
        return LowSignalDecision(False, None)

    if lowered in _ASCII_NOISE or compact in _CJK_NOISE:
        return LowSignalDecision(True, "known_noise_or_greeting")

    if compact.isdigit() and len(compact) <= 6:
        return LowSignalDecision(True, "short_numeric")

    if compact.isascii() and compact.isalpha() and len(compact) <= 8:
        return LowSignalDecision(True, "short_ascii_noise")

    if len(compact) <= 4:
        return LowSignalDecision(True, "too_short_without_business_signal")

    return LowSignalDecision(False, None)


def is_low_signal_customer_message(body: str | None, recent_context: list[dict[str, Any]] | None = None) -> bool:
    return classify_low_signal_customer_message(body, recent_context).is_low_signal
