from __future__ import annotations

from dataclasses import dataclass

DEFINITE_OPERATIONAL_CLAIM_TERMS = (
    "has been delivered",
    "was delivered",
    "is delivered",
    "will be delivered",
    "delivery today",
    "delivery tomorrow",
    "arrive today",
    "arrive tomorrow",
    "is out for delivery",
    "marked out for delivery",
    "status is failed delivery",
    "driver contacted",
    "refund approved",
    "compensation approved",
    "claim approved",
    "customs cleared",
    "customs released",
    "address changed",
    "rescheduled",
    "派送成功",
    "已经签收",
    "已签收",
    "已联系司机",
    "退款已批准",
    "赔付已批准",
    "清关完成",
    "地址已修改",
    "已改派",
    "今天送达",
    "明天送达",
    "预计送达",
)
STATUS_CHECK_CONTEXTS = (
    "check whether it is out for delivery",
    "check if it is out for delivery",
    "whether it is out for delivery",
    "if it is out for delivery",
    "whether the parcel is out for delivery",
    "if the parcel is out for delivery",
    "whether the package is out for delivery",
    "if the package is out for delivery",
    "whether the shipment is out for delivery",
    "if the shipment is out for delivery",
)


@dataclass(frozen=True)
class FactGateDecision:
    allowed: bool
    safety_level: str
    reason: str | None = None
    fact_evidence_present: bool = False


def evaluate_webchat_fact_gate(text: str | None, *, fact_evidence_present: bool = False, allow_tracking_status_card: bool = False) -> FactGateDecision:
    body = (text or "").strip().lower()
    if fact_evidence_present:
        return FactGateDecision(True, "allow", None, True)
    claim_body = body
    for context in STATUS_CHECK_CONTEXTS:
        claim_body = claim_body.replace(context, "")
    if any(term.lower() in claim_body for term in DEFINITE_OPERATIONAL_CLAIM_TERMS):
        return FactGateDecision(False, "block", "missing_business_or_tool_evidence", False)
    return FactGateDecision(True, "allow", None, False)
