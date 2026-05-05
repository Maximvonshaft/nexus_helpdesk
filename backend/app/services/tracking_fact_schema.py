from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

TRACKING_FACT_SOURCE = "speedaf_readonly_adapter"
TRACKING_FACT_TOOL_NAME = "speedaf_tracking_readonly_adapter"


def hash_tracking_number(tracking_number: str | None) -> str | None:
    value = (tracking_number or "").strip().upper()
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_tracking_number(tracking_number: str | None) -> str | None:
    value = (tracking_number or "").strip().upper()
    if not value:
        return None
    return f"****{value[-4:]}"


@dataclass(frozen=True)
class TrackingFactEvent:
    event_time: str | None = None
    location: str | None = None
    description: str | None = None
    milestone: str | None = None
    status: str | None = None

    def to_safe_dict(self) -> dict[str, str | None]:
        return {
            "event_time": self.event_time,
            "location": self.location,
            "description": self.description,
            "milestone": self.milestone,
            "status": self.status,
        }

    def is_present(self) -> bool:
        return bool(
            (self.event_time or "").strip()
            or (self.location or "").strip()
            or (self.description or "").strip()
            or (self.milestone or "").strip()
            or (self.status or "").strip()
        )


@dataclass(frozen=True)
class TrackingFactResult:
    ok: bool
    tracking_number: str | None = None
    tracking_number_masked: str | None = None
    tracking_hash: str | None = None
    status: str | None = None
    status_label: str | None = None
    latest_milestone: str | None = None
    latest_event: TrackingFactEvent | None = None
    events_summary: list[TrackingFactEvent] = field(default_factory=list)
    checked_at: str | None = None
    source: str = TRACKING_FACT_SOURCE
    tool_name: str = TRACKING_FACT_TOOL_NAME
    tool_status: str | None = None
    pii_redacted: bool = False
    raw_included: bool = False
    summary_safe: str | None = None
    message_safe: str | None = None
    risk_level: str | None = None
    escalate: bool = False
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
            "tracking_number_hash": self.tracking_hash or hash_tracking_number(self.tracking_number),
            "tracking_number_masked": self.tracking_number_masked or mask_tracking_number(self.tracking_number),
            "tracking_fact_raw_included": self.raw_included,
            "tracking_fact_risk_level": self.risk_level,
            "tracking_fact_escalate": self.escalate,
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
            f"- Tracking number: {self.tracking_number_masked or mask_tracking_number(self.tracking_number) or 'provided by customer'}",
            f"- Current status: {self.status_label or self.status or 'unknown'}",
            f"- Current milestone: {self.latest_milestone or 'unknown'}",
            f"- PII redacted: {str(self.pii_redacted).lower()}",
            f"- Raw included: {str(self.raw_included).lower()}",
        ]
        if self.summary_safe:
            lines.append(f"- Safe summary: {self.summary_safe}")
        if self.latest_event and self.latest_event.is_present():
            event = self.latest_event
            latest_parts = [
                part for part in [event.status or event.description, event.location, event.event_time] if part
            ]
            if latest_parts:
                lines.append(f"- Latest event: {' | '.join(latest_parts)}")
        safe_events = [event for event in self.events_summary if event.is_present()][:3]
        if safe_events:
            lines.append("- Recent safe events:")
            for event in safe_events:
                parts = [
                    part for part in [event.status or event.description, event.location, event.event_time] if part
                ]
                if parts:
                    lines.append(f"  - {' | '.join(parts)}")
        lines.extend([
            "Rules:",
            "Use only the trusted tracking fact above for parcel status.",
            "Do not mention internal tools, Bridge, OpenClaw, or raw tool output.",
            "Do not reveal recipient names, POD signer names, courier names, phone numbers, emails, or detailed addresses.",
        ])
        return "\n".join(lines)
