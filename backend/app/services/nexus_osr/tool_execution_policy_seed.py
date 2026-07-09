from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...models_osr import ToolExecutionPolicyRecord


@dataclass(frozen=True)
class ToolExecutionPolicySeed:
    tool_name: str
    enabled: bool
    ai_auto_executable: bool
    risk_level: str
    requires_tracking_number: bool = False
    requires_contact: bool = False
    requires_customer_confirmation: bool = False
    requires_human_confirmation: bool = False
    allowed_channels_json: list[str] | None = None
    allowed_countries_json: list[str] | None = None
    audit_level: str = "standard"


DEFAULT_TOOL_EXECUTION_POLICY_SEEDS: tuple[ToolExecutionPolicySeed, ...] = (
    ToolExecutionPolicySeed(
        tool_name="ticket.create",
        enabled=True,
        ai_auto_executable=True,
        risk_level="medium",
        audit_level="standard",
    ),
    ToolExecutionPolicySeed(
        tool_name="handoff.request.create",
        enabled=True,
        ai_auto_executable=True,
        risk_level="medium",
        audit_level="standard",
    ),
    ToolExecutionPolicySeed(
        tool_name="timeline.event.create",
        enabled=True,
        ai_auto_executable=True,
        risk_level="low",
        audit_level="standard",
    ),
    ToolExecutionPolicySeed(
        tool_name="speedaf.workOrder.create",
        enabled=False,
        ai_auto_executable=False,
        risk_level="high",
        requires_tracking_number=True,
        requires_contact=True,
        requires_human_confirmation=True,
        audit_level="strict",
    ),
)


def seed_default_tool_execution_policies(
    db: Session,
    *,
    country_code: str = "GLOBAL",
    channel: str = "all",
    overwrite_existing: bool = False,
) -> list[ToolExecutionPolicyRecord]:
    """Seed safe OSR ToolExecutionPolicy rows for controlled runtime actions.

    The seed is conservative: low/medium Nexus-owned actions are enabled and
    AI-auto-executable, while high-risk Speedaf write tools remain disabled by
    default. Existing rows are left unchanged unless `overwrite_existing=True`.
    """

    rows: list[ToolExecutionPolicyRecord] = []
    for seed in DEFAULT_TOOL_EXECUTION_POLICY_SEEDS:
        row = (
            db.query(ToolExecutionPolicyRecord)
            .filter(ToolExecutionPolicyRecord.tool_name == seed.tool_name)
            .filter(ToolExecutionPolicyRecord.country_code == country_code)
            .filter(ToolExecutionPolicyRecord.channel == channel)
            .first()
        )
        if row is None:
            row = ToolExecutionPolicyRecord(
                tool_name=seed.tool_name,
                country_code=country_code,
                channel=channel,
            )
            db.add(row)
        elif not overwrite_existing:
            rows.append(row)
            continue
        row.enabled = seed.enabled
        row.ai_auto_executable = seed.ai_auto_executable
        row.risk_level = seed.risk_level
        row.requires_tracking_number = seed.requires_tracking_number
        row.requires_contact = seed.requires_contact
        row.requires_customer_confirmation = seed.requires_customer_confirmation
        row.requires_human_confirmation = seed.requires_human_confirmation
        row.allowed_channels_json = list(seed.allowed_channels_json) if seed.allowed_channels_json else None
        row.allowed_countries_json = list(seed.allowed_countries_json) if seed.allowed_countries_json else None
        row.audit_level = seed.audit_level
        rows.append(row)
    db.flush()
    return rows
