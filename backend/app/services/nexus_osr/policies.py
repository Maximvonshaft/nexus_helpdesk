from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo


class HumanAvailabilityStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


class EscalationAction(StrEnum):
    TRY_AI_RESOLUTION = "try_ai_resolution"
    REQUEST_HANDOFF = "request_handoff"
    CREATE_TICKET = "create_ticket"
    HANDOFF_OR_TICKET = "handoff_or_ticket"


@dataclass(frozen=True)
class HumanAvailabilityDecision:
    status: HumanAvailabilityStatus
    queue_key: str
    reason: str
    customer_message_template: str | None = None
    auto_ticket_when_offline: bool = False

    @property
    def is_online(self) -> bool:
        return self.status == HumanAvailabilityStatus.ONLINE


@dataclass(frozen=True)
class HumanHoursPolicy:
    queue_key: str
    timezone_name: str = "UTC"
    enabled: bool = True
    # Example: {"mon": [("09:00", "18:00")], "tue": [("09:00", "18:00")]}
    weekly_hours: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    holidays: set[str] = field(default_factory=set)
    offline_message_template: str = (
        "Our human support team is currently offline. I can create a ticket so "
        "the team can follow up during working hours."
    )
    auto_ticket_when_offline: bool = True

    def evaluate(self, now: datetime | None = None) -> HumanAvailabilityDecision:
        if not self.enabled:
            return HumanAvailabilityDecision(
                status=HumanAvailabilityStatus.DISABLED,
                queue_key=self.queue_key,
                reason="handoff_disabled",
                customer_message_template=self.offline_message_template,
                auto_ticket_when_offline=self.auto_ticket_when_offline,
            )
        tz = ZoneInfo(self.timezone_name)
        current = (now or datetime.now(tz)).astimezone(tz)
        if current.date().isoformat() in self.holidays:
            return HumanAvailabilityDecision(
                status=HumanAvailabilityStatus.OFFLINE,
                queue_key=self.queue_key,
                reason="holiday",
                customer_message_template=self.offline_message_template,
                auto_ticket_when_offline=self.auto_ticket_when_offline,
            )
        day_key = current.strftime("%a").lower()[:3]
        windows = self.weekly_hours.get(day_key, [])
        for start, end in windows:
            if _parse_hhmm(start) <= current.time() <= _parse_hhmm(end):
                return HumanAvailabilityDecision(
                    status=HumanAvailabilityStatus.ONLINE,
                    queue_key=self.queue_key,
                    reason="within_working_hours",
                )
        return HumanAvailabilityDecision(
            status=HumanAvailabilityStatus.OFFLINE,
            queue_key=self.queue_key,
            reason="outside_working_hours",
            customer_message_template=self.offline_message_template,
            auto_ticket_when_offline=self.auto_ticket_when_offline,
        )


@dataclass(frozen=True)
class EscalationDecision:
    matched: bool
    risk_key: str | None = None
    action: EscalationAction = EscalationAction.TRY_AI_RESOLUTION
    reason: str | None = None
    max_ai_attempts: int = 2
    forbidden_commitments: list[str] = field(default_factory=list)

    @property
    def handoff_required(self) -> bool:
        return self.action in {
            EscalationAction.REQUEST_HANDOFF,
            EscalationAction.HANDOFF_OR_TICKET,
        }

    @property
    def ticket_required(self) -> bool:
        return self.action in {
            EscalationAction.CREATE_TICKET,
            EscalationAction.HANDOFF_OR_TICKET,
        }


@dataclass(frozen=True)
class EscalationPolicy:
    risk_key: str
    patterns: list[str]
    action: EscalationAction = EscalationAction.HANDOFF_OR_TICKET
    max_ai_attempts: int = 2
    forbidden_commitments: list[str] = field(default_factory=list)
    enabled: bool = True

    def evaluate(
        self,
        message: str,
        *,
        ai_attempt_count: int = 0,
    ) -> EscalationDecision:
        if not self.enabled:
            return EscalationDecision(matched=False)
        text = str(message or "")
        for pattern in self.patterns:
            if re.search(pattern, text, re.IGNORECASE):
                action = (
                    self.action
                    if ai_attempt_count >= self.max_ai_attempts
                    else EscalationAction.TRY_AI_RESOLUTION
                )
                return EscalationDecision(
                    matched=True,
                    risk_key=self.risk_key,
                    action=action,
                    reason=f"matched:{self.risk_key}",
                    max_ai_attempts=self.max_ai_attempts,
                    forbidden_commitments=list(self.forbidden_commitments),
                )
        return EscalationDecision(matched=False)


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    tool_name: str
    reason: str
    requires_customer_confirmation: bool = False
    requires_human_confirmation: bool = False
    missing_requirements: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolExecutionPolicy:
    tool_name: str
    enabled: bool = True
    ai_auto_executable: bool = False
    risk_level: str = "low"
    requires_tracking_number: bool = False
    requires_contact: bool = False
    requires_customer_confirmation: bool = False
    requires_human_confirmation: bool = False
    allowed_channels: set[str] = field(default_factory=set)
    allowed_countries: set[str] = field(default_factory=set)

    def evaluate(
        self,
        *,
        channel: str | None = None,
        country_code: str | None = None,
        has_tracking_number: bool = False,
        has_contact: bool = False,
    ) -> ToolPolicyDecision:
        missing: list[str] = []
        if not self.enabled:
            return ToolPolicyDecision(False, self.tool_name, "tool_disabled")

        confirmation_gated = bool(
            self.requires_customer_confirmation
            or self.requires_human_confirmation
        )
        if not self.ai_auto_executable and not confirmation_gated:
            return ToolPolicyDecision(
                False,
                self.tool_name,
                "tool_not_ai_auto_executable",
            )
        if self.allowed_channels and (channel or "") not in self.allowed_channels:
            return ToolPolicyDecision(
                False,
                self.tool_name,
                "channel_not_allowed",
            )
        if self.allowed_countries and (country_code or "") not in self.allowed_countries:
            return ToolPolicyDecision(
                False,
                self.tool_name,
                "country_not_allowed",
            )
        if self.requires_tracking_number and not has_tracking_number:
            missing.append("tracking_number")
        if self.requires_contact and not has_contact:
            missing.append("contact_method")
        if missing:
            return ToolPolicyDecision(
                False,
                self.tool_name,
                "missing_required_context",
                missing_requirements=missing,
            )
        return ToolPolicyDecision(
            True,
            self.tool_name,
            (
                "confirmation_required"
                if not self.ai_auto_executable and confirmation_gated
                else "allowed"
            ),
            requires_customer_confirmation=self.requires_customer_confirmation,
            requires_human_confirmation=self.requires_human_confirmation,
        )


def default_escalation_policies() -> list[EscalationPolicy]:
    return [
        EscalationPolicy(
            risk_key="compensation",
            patterns=[r"\bcompensation\b", r"\brefund\b", r"赔偿", r"退款", r"索赔"],
            forbidden_commitments=[
                "do_not_confirm_compensation",
                "do_not_confirm_refund",
            ],
        ),
        EscalationPolicy(
            risk_key="formal_complaint",
            patterns=[r"\bcomplain\b", r"\bcomplaint\b", r"投诉", r"客诉"],
            forbidden_commitments=[
                "do_not_claim_complaint_resolved_without_ticket"
            ],
        ),
        EscalationPolicy(
            risk_key="legal_threat",
            patterns=[r"\blawyer\b", r"\blegal\b", r"法院", r"律师", r"起诉"],
            action=EscalationAction.HANDOFF_OR_TICKET,
            max_ai_attempts=0,
        ),
    ]


def evaluate_escalation(
    message: str,
    *,
    ai_attempt_count: int = 0,
    policies: list[EscalationPolicy] | None = None,
) -> EscalationDecision:
    for policy in policies or default_escalation_policies():
        decision = policy.evaluate(
            message,
            ai_attempt_count=ai_attempt_count,
        )
        if decision.matched:
            return decision
    return EscalationDecision(matched=False)


def _parse_hhmm(value: str) -> time:
    hour, minute = str(value).split(":", 1)
    return time(hour=int(hour), minute=int(minute))
