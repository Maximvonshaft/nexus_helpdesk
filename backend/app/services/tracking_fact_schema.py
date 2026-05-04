from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

TRACKING_FACT_SOURCE = "openclaw_bridge.speedaf_lookup"
TRACKING_FACT_TOOL_NAME = "speedaf_lookup"


def hash_tracking_number(tracking_number: str | None) -> str | None:
    value = (tracking_number or "").strip().upper()
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TrackingFactEvent:
    event_time: str | None = None
    location: str | None = None
    description: str | None = None

    def to_safe_dict(self) -> dict[str, str | None]:
        return {
            "event_time": self.event_time,
            "location": self.location,
            "description": self.description,
        }

    def is_present(self) -> bool:
        return bool((self.event_time or "").strip() or (self.location or "").strip() or (self.description or "").strip())


@dataclass(frozen=True)
class TrackingFactResult:
    ok: bool
    tracking_number: str | None = None
    status: str | None = None
    status_label: str | None = None
    latest_event: TrackingFactEvent | None = None
    events_summary: list[TrackingFactEvent] = field(default_factory=list)
    checked_at: str | None = None
    source: str = TRACKING_FACT_SOURCE
    tool_name: str = TRACKING_FACT_TOOL_NAME
    tool_status: str | None = None
    pii_redacted: bool = False
    fact_evidence_present: bool = False
    failure_reason: str | None = None

    def metadata_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fact_evidence_present": self.fact_evidence_present,
            "fact_source": self.source,
            "tool_name": self.tool_name,
            "tool_status": self.tool_status,
            "pii_redacted": self.pii_redacted,
            "checked_at": self.checked_at,
            "tracking_number_hash": hash_tracking_number(self.tracking_number),
        }
        if self.failure_reason:
            payload["tracking_fact_failure_reason"] = self.failure_reason
        return {key: value for key, value in payload.items() if value is not None}

    def prompt_summary(self) -> str:
        if not self.fact_evidence_present:
            return ""
        lines = [
            "Trusted tracking fact:",
            f"- Source: {self.source}",
            f"- Checked at: {self.checked_at or 'unknown'}",
            f"- Tracking number: {self.tracking_number or 'provided by customer'}",
            f"- Current status: {self.status_label or self.status or 'unknown'}",
            f"- PII redacted: {str(self.pii_redacted).lower()}",
        ]
        if self.latest_event and self.latest_event.is_present():
            event = self.latest_event
            latest_parts = [part for part in [event.description, event.location, event.event_time] if part]
            if latest_parts:
                lines.append(f"- Latest event: {' | '.join(latest_parts)}")
        safe_events = [event for event in self.events_summary if event.is_present()][:3]
        if safe_events:
            lines.append("- Recent events:")
            for event in safe_events:
                parts = [part for part in [event.description, event.location, event.event_time] if part]
                if parts:
                    lines.append(f"  - {' | '.join(parts)}")
        lines.extend([
            "Rules:",
            "Use only the trusted tracking fact above for parcel status.",
            "Do not mention internal tools, Bridge, OpenClaw, or raw tool output.",
            "Do not reveal recipient names, POD signer names, phone numbers, emails, or detailed addresses.",
        ])
        return "\n".join(lines)
