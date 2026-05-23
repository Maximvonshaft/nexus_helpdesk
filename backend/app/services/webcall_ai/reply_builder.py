from __future__ import annotations

from ...services.tracking_fact_schema import TrackingFactResult

MISSING_TRACKING_REPLY = "Please provide your tracking number so I can check the parcel status."
LOOKUP_DISABLED_REPLY = (
    "I have the tracking number, but tracking lookup is not available right now. "
    "I can hand this to a human agent to check it."
)
LOOKUP_FAILED_REPLY = (
    "I could not confirm the parcel status from the trusted tracking source. "
    "Please verify the tracking number, or I can hand this to a human agent."
)
HANDOFF_REPLY = "This request needs a human agent. I will hand this call to the support team."


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
            return "I found multiple matching parcels. Please confirm the last four digits: " + ", ".join(suffixes) + "."
        return "I found multiple matching parcels. Please confirm the last four digits of the tracking number."

    if not fact.ok or not fact.fact_evidence_present:
        return LOOKUP_FAILED_REPLY

    status = fact.status_label or fact.status or "unknown"
    parts = [f"I found the latest tracking status: {status}."]
    if fact.latest_event and fact.latest_event.is_present():
        latest_parts = [
            part
            for part in [fact.latest_event.description, fact.latest_event.location, fact.latest_event.event_time]
            if part
        ]
        if latest_parts:
            parts.append("Latest event: " + " | ".join(latest_parts) + ".")
    if fact.checked_at:
        parts.append(f"Checked at: {fact.checked_at}.")
    return " ".join(parts)
