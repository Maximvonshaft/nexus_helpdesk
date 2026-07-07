from __future__ import annotations

from ...services.tracking_fact_schema import TrackingFactResult

MISSING_TRACKING_REPLY = ""
LOOKUP_DISABLED_REPLY = ""
LOOKUP_FAILED_REPLY = ""
HANDOFF_REPLY = ""


def build_missing_tracking_reply() -> str:
    return MISSING_TRACKING_REPLY


def build_tracking_lookup_disabled_reply() -> str:
    return LOOKUP_DISABLED_REPLY


def build_handoff_reply() -> str:
    return HANDOFF_REPLY


def build_tracking_reply(fact: TrackingFactResult) -> str:
    if fact.failure_reason == "multiple_waybill_candidates" and fact.safe_candidates:
        suffixes = [
            str(item.get("waybill_suffix"))
            for item in fact.safe_candidates
            if item.get("waybill_suffix")
        ][:5]
        if suffixes:
            return ""
        return ""

    if not fact.ok or not fact.fact_evidence_present:
        return LOOKUP_FAILED_REPLY

    return ""
