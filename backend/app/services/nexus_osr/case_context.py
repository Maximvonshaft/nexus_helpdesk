from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from ..tracking_fact_schema import hash_tracking_number, safe_tracking_reference


class CaseContextStatus(StrEnum):
    ACTIVE = "active"
    WAITING_CUSTOMER = "waiting_customer"
    HUMAN_REVIEW = "human_review"
    TICKET_CREATED = "ticket_created"
    ROUTED = "routed"
    CLOSED = "closed"
    ARCHIVED = "archived"


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_TRACKING_RE = re.compile(r"\b(?=[A-Z0-9-]{8,35}\b)(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]*[A-Z0-9]\b", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_case_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)

    def _tracking(match: re.Match[str]) -> str:
        token = match.group(0).upper().replace("-", "")
        # Avoid replacing ordinary long English words with no digits.
        if not any(char.isdigit() for char in token):
            return match.group(0)
        return f"tracking ending {token[-6:]}"

    text = _TRACKING_RE.sub(_tracking, text)
    return text[:limit]


def extract_tracking_reference(text: str | None) -> tuple[str | None, str | None]:
    for match in _TRACKING_RE.finditer(str(text or "")):
        token = match.group(0).strip().upper()
        if any(char.isdigit() for char in token):
            return safe_tracking_reference(token), hash_tracking_number(token)
    return None, None


@dataclass(frozen=True)
class ContactMethod:
    channel: str
    value_redacted: str
    source: str
    is_default: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "value_redacted": self.value_redacted,
            "source": self.source,
            "is_default": self.is_default,
        }


@dataclass(frozen=True)
class CaseContext:
    """Short-lived operational context for one support case.

    This object is intentionally not a customer profile.  It tracks the minimum
    state required to solve or route the current logistics support issue.
    """

    conversation_id: int | str | None = None
    ticket_id: int | str | None = None
    channel: str | None = None
    country_code: str | None = None
    issue_type: str | None = None
    status: CaseContextStatus = CaseContextStatus.ACTIVE
    safe_tracking_reference: str | None = None
    tracking_number_hash: str | None = None
    contact_methods: list[ContactMethod] = field(default_factory=list)
    customer_claim_summary: str | None = None
    last_mcp_fact: dict[str, Any] | None = None
    missing_info: list[str] = field(default_factory=list)
    handoff_requested: bool = False
    ticket_created: bool = False
    routed_group_key: str | None = None
    ai_actions_taken: list[str] = field(default_factory=list)
    agent_handover_summary: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    closed_at: str | None = None

    def with_inbound_message(self, text: str, *, channel: str | None = None, country_code: str | None = None) -> "CaseContext":
        safe_ref, tracking_hash = extract_tracking_reference(text)
        next_actions = list(self.ai_actions_taken)
        if safe_ref and "tracking_reference_captured" not in next_actions:
            next_actions.append("tracking_reference_captured")
        return replace(
            self,
            channel=channel or self.channel,
            country_code=country_code or self.country_code,
            safe_tracking_reference=safe_ref or self.safe_tracking_reference,
            tracking_number_hash=tracking_hash or self.tracking_number_hash,
            customer_claim_summary=redact_case_text(text, limit=240) or self.customer_claim_summary,
            ai_actions_taken=next_actions,
            updated_at=utc_now_iso(),
        )

    def with_contact_method(self, *, channel: str, value: str, source: str, is_default: bool = False) -> "CaseContext":
        redacted = redact_case_text(value, limit=120)
        existing = [item for item in self.contact_methods if not (item.channel == channel and item.value_redacted == redacted)]
        existing.append(ContactMethod(channel=channel, value_redacted=redacted, source=source, is_default=is_default))
        missing = [item for item in self.missing_info if item != "contact_method"]
        return replace(self, contact_methods=existing, missing_info=missing, updated_at=utc_now_iso())

    def require_missing_info(self, *fields: str) -> "CaseContext":
        missing = list(dict.fromkeys([*self.missing_info, *[field for field in fields if field]]))
        return replace(self, missing_info=missing, status=CaseContextStatus.WAITING_CUSTOMER, updated_at=utc_now_iso())

    def with_mcp_fact(self, fact: dict[str, Any]) -> "CaseContext":
        safe_fact = dict(fact or {})
        if safe_fact.get("tracking_number"):
            safe_fact.pop("tracking_number", None)
        return replace(self, last_mcp_fact=safe_fact, updated_at=utc_now_iso())

    def mark_handoff_requested(self, *, summary: str | None = None) -> "CaseContext":
        return replace(
            self,
            handoff_requested=True,
            status=CaseContextStatus.HUMAN_REVIEW,
            agent_handover_summary=redact_case_text(summary, limit=500) or self.agent_handover_summary,
            updated_at=utc_now_iso(),
        )

    def mark_ticket_created(self, ticket_id: int | str) -> "CaseContext":
        return replace(
            self,
            ticket_id=ticket_id,
            ticket_created=True,
            status=CaseContextStatus.TICKET_CREATED,
            updated_at=utc_now_iso(),
        )

    def mark_routed(self, group_key: str) -> "CaseContext":
        return replace(self, routed_group_key=group_key, status=CaseContextStatus.ROUTED, updated_at=utc_now_iso())

    def close(self) -> "CaseContext":
        now = utc_now_iso()
        return replace(self, status=CaseContextStatus.CLOSED, closed_at=now, updated_at=now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "ticket_id": self.ticket_id,
            "channel": self.channel,
            "country_code": self.country_code,
            "issue_type": self.issue_type,
            "status": str(self.status),
            "safe_tracking_reference": self.safe_tracking_reference,
            "tracking_number_hash": self.tracking_number_hash,
            "contact_methods": [item.as_dict() for item in self.contact_methods],
            "customer_claim_summary": self.customer_claim_summary,
            "last_mcp_fact": self.last_mcp_fact,
            "missing_info": list(self.missing_info),
            "handoff_requested": self.handoff_requested,
            "ticket_created": self.ticket_created,
            "routed_group_key": self.routed_group_key,
            "ai_actions_taken": list(self.ai_actions_taken),
            "agent_handover_summary": self.agent_handover_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
        }
