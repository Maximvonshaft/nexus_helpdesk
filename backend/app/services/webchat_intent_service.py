from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .webchat_fact_gate import evaluate_webchat_fact_gate

INTENT_ALLOWLIST = {
    "greeting",
    "tracking",
    "address_change",
    "reschedule",
    "complaint",
    "request_photo",
    "handoff",
    "collect_missing_fields",
    "unknown",
}
HIGH_RISK_TERMS = (
    "refund", "compensation", "lost", "damaged", "customs", "claim", "legal", "complaint", "address change",
    "reschedule", "change address", "delivery time", "driver contacted", "delivered", "customs cleared",
    "赔偿", "赔付", "退款", "丢件", "破损", "海关", "清关", "投诉", "改地址", "改派", "索赔", "已签收",
)
TRACKING_RE = re.compile(r"\b[A-Z0-9]{8,30}\b", re.IGNORECASE)


@dataclass(frozen=True)
class WebChatIntent:
    intent: str
    confidence: float
    language: str
    missing_fields: list[str]
    risk_level: str
    recommended_card: str | None
    customer_reply: str | None

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def detect_webchat_intent(text: str | None) -> WebChatIntent:
    raw = text or ""
    normalized = f" {raw.lower()} "
    language = "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in raw) else "en"

    fact_decision = evaluate_webchat_fact_gate(raw, fact_evidence_present=False, allow_tracking_status_card=False)
    if not fact_decision.allowed:
        return WebChatIntent(
            intent="handoff",
            confidence=0.9,
            language=language,
            missing_fields=[],
            risk_level="high",
            recommended_card="handoff",
            customer_reply=None,
        )

    high_risk = any(term.lower() in normalized for term in HIGH_RISK_TERMS)
    if high_risk:
        intent = "handoff" if "complaint" not in normalized and "投诉" not in raw else "complaint"
        return WebChatIntent(intent=intent, confidence=0.86, language=language, missing_fields=[], risk_level="high", recommended_card="handoff", customer_reply=None)
    if any(k in normalized for k in (" track ", "tracking", "parcel", "package", "shipment", "delivery", "where is", "单号", "运单", "物流", "包裹", "快递", "派送")):
        missing = [] if TRACKING_RE.search(raw) else ["tracking_number"]
        return WebChatIntent(intent="tracking", confidence=0.82, language=language, missing_fields=missing, risk_level="medium" if missing else "low", recommended_card="quick_replies", customer_reply=None)
    if any(k in normalized for k in ("human", "agent", "support", "representative", "人工", "客服", "真人")):
        return WebChatIntent(intent="handoff", confidence=0.88, language=language, missing_fields=[], risk_level="medium", recommended_card="handoff", customer_reply=None)
    if any(k in normalized for k in ("hello", "hi", "hey", "你好", "您好")):
        return WebChatIntent(intent="greeting", confidence=0.72, language=language, missing_fields=[], risk_level="low", recommended_card="quick_replies", customer_reply=None)
    return WebChatIntent(intent="unknown", confidence=0.35, language=language, missing_fields=[], risk_level="low", recommended_card="quick_replies", customer_reply=None)
