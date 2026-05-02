from __future__ import annotations

import re
from dataclasses import dataclass

DANGEROUS_FACT_TERMS = (
    "delivered", "will be delivered", "driver contacted", "refund approved", "compensation approved", "customs cleared",
    "address changed", "rescheduled", "派送成功", "已经签收", "已联系司机", "退款已批准", "赔付已批准", "清关完成", "地址已修改", "已改派",
)
TRACKING_STATUS_TERMS = ("in transit", "out for delivery", "delivered", "failed delivery", "派送中", "运输中", "已签收", "派送失败")


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
    if any(term.lower() in body for term in DANGEROUS_FACT_TERMS):
        return FactGateDecision(False, "block", "missing_business_or_tool_evidence", False)
    if any(term.lower() in body for term in TRACKING_STATUS_TERMS) and not allow_tracking_status_card:
        return FactGateDecision(False, "block", "missing_tracking_tool_result", False)
    if re.search(r"\bETA\b|\barriv(e|al)\b|\btomorrow\b|\btoday\b", body, re.IGNORECASE):
        return FactGateDecision(False, "review", "possible_unverified_delivery_time", False)
    return FactGateDecision(True, "allow", None, False)
