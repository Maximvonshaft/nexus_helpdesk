from __future__ import annotations

import re
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .services.nexus_osr.case_context import CaseContextStatus
from .services.nexus_osr.policies import EscalationAction
from .services.webchat_ai_decision_runtime.tool_registry import canonical_tool_name, get_tool_contract

DAY_KEYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
FALLBACK_ACTIONS = {"create_ticket", "handoff", "handoff_or_ticket", "null_reply", "clarification"}
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
ISSUE_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        value = " ".join(value.strip().split())
        return value or None
    return value


def _norm_country(value: str | None) -> str:
    return str(_strip(value) or "GLOBAL").upper()[:16]


def _norm_optional_country(value: str | None) -> str | None:
    return None if value in (None, "") else _norm_country(value)


def _norm_channel(value: str | None, default: str = "all") -> str:
    return str(_strip(value) or default).lower()[:40]


def _norm_optional_channel(value: str | None) -> str | None:
    return None if value in (None, "") else _norm_channel(value)


def _validate_hhmm(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value.strip()):
        raise ValueError("working hours must use HH:MM")
    hour, minute = value.split(":", 1)
    if int(hour) > 23 or int(minute) > 59:
        raise ValueError("working hours must use valid HH:MM")
    return value


def _validate_working_hours(value: Any) -> dict[str, list[list[str]]] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("working_hours_json must be an object")
    output: dict[str, list[list[str]]] = {}
    for raw_day, windows in value.items():
        day = str(raw_day).strip().lower()[:3]
        if day not in DAY_KEYS:
            raise ValueError("working_hours_json day keys must be mon..sun")
        if not isinstance(windows, list):
            raise ValueError("working_hours_json day value must be a list")
        parsed_windows: list[list[str]] = []
        for window in windows:
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise ValueError("working hour windows must be [start, end]")
            start = _validate_hhmm(str(window[0]))
            end = _validate_hhmm(str(window[1]))
            if start >= end:
                raise ValueError("working hour start must be earlier than end")
            parsed_windows.append([start, end])
        output[day] = parsed_windows
    return output


def _validate_holidays(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ValueError("holiday_calendar_json must be a list")
    output: list[str] = []
    for item in value[:366]:
        if not isinstance(item, str):
            raise ValueError("holiday values must be YYYY-MM-DD strings")
        cleaned = item.strip()
        try:
            date.fromisoformat(cleaned)
        except ValueError as exc:
            raise ValueError("holiday values must be valid YYYY-MM-DD dates") from exc
        output.append(cleaned)
    return output


def _validate_regex_list(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ValueError("patterns must be a list")
    output: list[str] = []
    for item in value[:100]:
        pattern = str(item or "").strip()
        if not pattern:
            continue
        if len(pattern) > 500:
            raise ValueError("regex pattern is too long")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("invalid regex pattern") from exc
        output.append(pattern)
    return output


def _validate_string_list(value: Any, *, max_items: int = 50, max_chars: int = 160) -> list[str] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ValueError("value must be a list")
    output: list[str] = []
    for item in value[:max_items]:
        cleaned = str(item or "").strip()
        if cleaned:
            output.append(cleaned[:max_chars])
    return output


def _validate_tool_name(value: Any) -> str:
    cleaned = canonical_tool_name(str(_strip(value) or ""))
    if not cleaned or get_tool_contract(cleaned) is None:
        raise ValueError("tool_name must exist in Tool Registry")
    return cleaned


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone_name must be a valid IANA timezone") from exc
    return value


def _validate_fallback_action(value: str) -> str:
    if value not in FALLBACK_ACTIONS:
        raise ValueError("fallback_action is not allowed")
    return value


def _validate_escalation_action(value: str) -> str:
    try:
        EscalationAction(value)
    except ValueError as exc:
        raise ValueError("action must be a valid escalation action") from exc
    return value


def _validate_risk(value: str) -> str:
    cleaned = str(value or "low").strip().lower()
    if cleaned not in {"low", "medium", "high", "critical"}:
        raise ValueError("risk_level must be low, medium, high, or critical")
    return cleaned


class OSRAdminModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HumanHoursPolicyCreate(OSRAdminModel):
    country_code: str = Field(default="GLOBAL", max_length=16)
    channel: str = Field(default="all", max_length=40)
    queue_key: str = Field(min_length=1, max_length=120)
    timezone_name: str = Field(default="UTC", max_length=80)
    working_hours_json: dict[str, list[list[str]]] | None = None
    holiday_calendar_json: list[str] | None = None
    handoff_enabled: bool = True
    offline_message_template: str | None = Field(default=None, max_length=2000)
    auto_ticket_when_offline: bool = True
    customer_wait_timeout_seconds: int = Field(default=180, ge=0, le=86400)
    fallback_action: str = Field(default="create_ticket", max_length=80)
    enabled: bool = True

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str:
        return _norm_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        return _norm_channel(value)

    @field_validator("queue_key", "offline_message_template", "fallback_action", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return _validate_timezone(value)

    @field_validator("working_hours_json", mode="before")
    @classmethod
    def validate_hours(cls, value: Any) -> Any:
        return _validate_working_hours(value)

    @field_validator("holiday_calendar_json", mode="before")
    @classmethod
    def validate_holidays(cls, value: Any) -> Any:
        return _validate_holidays(value)

    @field_validator("fallback_action")
    @classmethod
    def validate_fallback_action(cls, value: str) -> str:
        return _validate_fallback_action(value)


class HumanHoursPolicyUpdate(OSRAdminModel):
    country_code: str | None = Field(default=None, max_length=16)
    channel: str | None = Field(default=None, max_length=40)
    queue_key: str | None = Field(default=None, min_length=1, max_length=120)
    timezone_name: str | None = Field(default=None, max_length=80)
    working_hours_json: dict[str, list[list[str]]] | None = None
    holiday_calendar_json: list[str] | None = None
    handoff_enabled: bool | None = None
    offline_message_template: str | None = Field(default=None, max_length=2000)
    auto_ticket_when_offline: bool | None = None
    customer_wait_timeout_seconds: int | None = Field(default=None, ge=0, le=86400)
    fallback_action: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str | None:
        return _norm_optional_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str | None:
        return _norm_optional_channel(value)

    @field_validator("queue_key", "offline_message_template", "fallback_action", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return None if value is None else _validate_timezone(value)

    @field_validator("working_hours_json", mode="before")
    @classmethod
    def validate_hours(cls, value: Any) -> Any:
        return _validate_working_hours(value)

    @field_validator("holiday_calendar_json", mode="before")
    @classmethod
    def validate_holidays(cls, value: Any) -> Any:
        return _validate_holidays(value)

    @field_validator("fallback_action")
    @classmethod
    def validate_fallback_action(cls, value: str | None) -> str | None:
        return None if value is None else _validate_fallback_action(value)


class EscalationPolicyCreate(OSRAdminModel):
    risk_key: str = Field(min_length=1, max_length=120)
    country_code: str = Field(default="GLOBAL", max_length=16)
    channel: str = Field(default="all", max_length=40)
    trigger_patterns_json: list[str] | None = None
    semantic_intents_json: list[str] | None = None
    max_ai_attempts: int = Field(default=2, ge=0, le=20)
    action: str = Field(default="handoff_or_ticket", max_length=80)
    handoff_required: bool = True
    ticket_required: bool = True
    forbidden_commitments_json: list[str] | None = None
    allowed_resolution_actions_json: list[str] | None = None
    enabled: bool = True

    @field_validator("risk_key", "action", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str:
        return _norm_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        return _norm_channel(value)

    @field_validator("trigger_patterns_json", mode="before")
    @classmethod
    def validate_regex_patterns(cls, value: Any) -> Any:
        return _validate_regex_list(value)

    @field_validator("semantic_intents_json", "forbidden_commitments_json", "allowed_resolution_actions_json", mode="before")
    @classmethod
    def validate_lists(cls, value: Any) -> Any:
        return _validate_string_list(value)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        return _validate_escalation_action(value)


class EscalationPolicyUpdate(OSRAdminModel):
    risk_key: str | None = Field(default=None, min_length=1, max_length=120)
    country_code: str | None = Field(default=None, max_length=16)
    channel: str | None = Field(default=None, max_length=40)
    trigger_patterns_json: list[str] | None = None
    semantic_intents_json: list[str] | None = None
    max_ai_attempts: int | None = Field(default=None, ge=0, le=20)
    action: str | None = Field(default=None, max_length=80)
    handoff_required: bool | None = None
    ticket_required: bool | None = None
    forbidden_commitments_json: list[str] | None = None
    allowed_resolution_actions_json: list[str] | None = None
    enabled: bool | None = None

    @field_validator("risk_key", "action", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str | None:
        return _norm_optional_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str | None:
        return _norm_optional_channel(value)

    @field_validator("trigger_patterns_json", mode="before")
    @classmethod
    def validate_regex_patterns(cls, value: Any) -> Any:
        return _validate_regex_list(value)

    @field_validator("semantic_intents_json", "forbidden_commitments_json", "allowed_resolution_actions_json", mode="before")
    @classmethod
    def validate_lists(cls, value: Any) -> Any:
        return _validate_string_list(value)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str | None) -> str | None:
        return None if value is None else _validate_escalation_action(value)


class ToolExecutionPolicyCreate(OSRAdminModel):
    tool_name: str = Field(min_length=1, max_length=160)
    country_code: str = Field(default="GLOBAL", max_length=16)
    channel: str = Field(default="all", max_length=40)
    enabled: bool = True
    ai_auto_executable: bool = False
    risk_level: str = Field(default="low", max_length=40)
    requires_tracking_number: bool = False
    requires_contact: bool = False
    requires_customer_confirmation: bool = False
    requires_human_confirmation: bool = False
    allowed_channels_json: list[str] | None = None
    allowed_countries_json: list[str] | None = None
    customer_visible_success_template: str | None = Field(default=None, max_length=2000)
    customer_visible_failure_template: str | None = Field(default=None, max_length=2000)
    audit_level: str = Field(default="standard", max_length=80)

    @field_validator("tool_name", mode="before")
    @classmethod
    def validate_tool(cls, value: Any) -> str:
        return _validate_tool_name(value)

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str:
        return _norm_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        return _norm_channel(value)

    @field_validator("risk_level")
    @classmethod
    def validate_risk(cls, value: str) -> str:
        return _validate_risk(value)

    @field_validator("allowed_channels_json", "allowed_countries_json", mode="before")
    @classmethod
    def validate_lists(cls, value: Any) -> Any:
        return _validate_string_list(value)

    @field_validator("customer_visible_success_template", "customer_visible_failure_template", "audit_level", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)


class ToolExecutionPolicyUpdate(OSRAdminModel):
    tool_name: str | None = Field(default=None, min_length=1, max_length=160)
    country_code: str | None = Field(default=None, max_length=16)
    channel: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None
    ai_auto_executable: bool | None = None
    risk_level: str | None = Field(default=None, max_length=40)
    requires_tracking_number: bool | None = None
    requires_contact: bool | None = None
    requires_customer_confirmation: bool | None = None
    requires_human_confirmation: bool | None = None
    allowed_channels_json: list[str] | None = None
    allowed_countries_json: list[str] | None = None
    customer_visible_success_template: str | None = Field(default=None, max_length=2000)
    customer_visible_failure_template: str | None = Field(default=None, max_length=2000)
    audit_level: str | None = Field(default=None, max_length=80)

    @field_validator("tool_name", mode="before")
    @classmethod
    def validate_tool(cls, value: Any) -> str | None:
        return None if value in (None, "") else _validate_tool_name(value)

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str | None:
        return _norm_optional_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str | None:
        return _norm_optional_channel(value)

    @field_validator("risk_level")
    @classmethod
    def validate_risk(cls, value: str | None) -> str | None:
        return None if value is None else _validate_risk(value)

    @field_validator("allowed_channels_json", "allowed_countries_json", mode="before")
    @classmethod
    def validate_lists(cls, value: Any) -> Any:
        return _validate_string_list(value)

    @field_validator("customer_visible_success_template", "customer_visible_failure_template", "audit_level", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)


class WhatsAppRoutingRuleCreate(OSRAdminModel):
    country_code: str = Field(max_length=16)
    issue_type: str = Field(min_length=1, max_length=120)
    channel: str = Field(default="whatsapp", max_length=40)
    destination_group_id: str = Field(min_length=1, max_length=200)
    fallback_group_id: str | None = Field(default=None, max_length=200)
    working_hours_key: str | None = Field(default=None, max_length=120)
    message_template: str | None = Field(default=None, max_length=2000)
    priority: int = Field(default=100, ge=0, le=1_000_000)
    enabled: bool = True

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str:
        return _norm_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        return _norm_channel(value, default="whatsapp")

    @field_validator("issue_type", "destination_group_id", "fallback_group_id", "working_hours_key", "message_template", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)


class WhatsAppRoutingRuleUpdate(OSRAdminModel):
    country_code: str | None = Field(default=None, max_length=16)
    issue_type: str | None = Field(default=None, min_length=1, max_length=120)
    channel: str | None = Field(default=None, max_length=40)
    destination_group_id: str | None = Field(default=None, min_length=1, max_length=200)
    fallback_group_id: str | None = Field(default=None, max_length=200)
    working_hours_key: str | None = Field(default=None, max_length=120)
    message_template: str | None = Field(default=None, max_length=2000)
    priority: int | None = Field(default=None, ge=0, le=1_000_000)
    enabled: bool | None = None

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str | None:
        return _norm_optional_country(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str | None:
        return _norm_optional_channel(value)

    @field_validator("issue_type", "destination_group_id", "fallback_group_id", "working_hours_key", "message_template", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)


class CaseContextSafeUpdate(OSRAdminModel):
    status: CaseContextStatus | None = None
    issue_type: str | None = Field(default=None, min_length=1, max_length=120)
    routed_group_key: str | None = Field(default=None, min_length=1, max_length=160)
    handoff_requested: bool | None = None
    agent_handover_summary: str | None = Field(default=None, max_length=600)
    missing_info_json: list[str] | None = None

    @field_validator("issue_type", "routed_group_key", "agent_handover_summary", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return _strip(value)

    @field_validator("issue_type")
    @classmethod
    def validate_issue_type(cls, value: str | None) -> str | None:
        if value is not None and not ISSUE_TYPE_RE.fullmatch(value):
            raise ValueError("issue_type must be a safe configuration key")
        return value

    @field_validator("routed_group_key")
    @classmethod
    def validate_routed_group_key(cls, value: str | None) -> str | None:
        if value is not None and not SAFE_KEY_RE.fullmatch(value):
            raise ValueError("routed_group_key must be a safe configuration key")
        return value

    @field_validator("missing_info_json", mode="before")
    @classmethod
    def validate_missing_info(cls, value: Any) -> list[str] | None:
        items = _validate_string_list(value, max_items=30, max_chars=120)
        if items is not None and any(not SAFE_KEY_RE.fullmatch(item) for item in items):
            raise ValueError("missing_info_json entries must be safe configuration keys")
        return items

    @model_validator(mode="after")
    def require_change(self) -> "CaseContextSafeUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one safe field is required")
        return self
