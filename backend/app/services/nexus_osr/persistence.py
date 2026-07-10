from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models_osr import (
    EscalationPolicyRecord,
    HumanHoursPolicyRecord,
    RuntimeDecisionAuditRecord,
    ToolExecutionPolicyRecord,
    WhatsAppRoutingRuleRecord,
)
from .audit_sanitizer import safe_audit_label, sanitize_audit_payload
from .case_context import CaseContext
from .case_context_persistence import (
    close_case_context,
    expire_case_context,
    load_case_context,
    record_to_case_context,
    save_case_context,
)
from .policies import EscalationAction, EscalationPolicy, HumanHoursPolicy, ToolExecutionPolicy
from .runtime_decision_contract import RuntimeDecision, RuntimeDecisionEvaluation


def resolve_human_hours_policy(db: Session, *, country_code: str | None, channel: str | None, queue_key: str) -> HumanHoursPolicy | None:
    row = _best_scoped_row(
        db,
        HumanHoursPolicyRecord,
        country_code=country_code,
        channel=channel,
        extra_filters=[HumanHoursPolicyRecord.queue_key == queue_key, HumanHoursPolicyRecord.enabled.is_(True)],
    )
    if not row:
        return None
    return HumanHoursPolicy(
        queue_key=row.queue_key,
        timezone_name=row.timezone_name,
        enabled=row.enabled and row.handoff_enabled,
        weekly_hours=_weekly_hours(row.working_hours_json),
        holidays=set(str(item) for item in (row.holiday_calendar_json or [])),
        offline_message_template=row.offline_message_template or HumanHoursPolicy(queue_key=row.queue_key).offline_message_template,
        auto_ticket_when_offline=row.auto_ticket_when_offline,
    )


def load_escalation_policies(db: Session, *, country_code: str | None, channel: str | None) -> list[EscalationPolicy]:
    rows = (
        db.query(EscalationPolicyRecord)
        .filter(EscalationPolicyRecord.enabled.is_(True))
        .filter(EscalationPolicyRecord.country_code.in_([country_code or "GLOBAL", "GLOBAL"]))
        .filter(EscalationPolicyRecord.channel.in_([channel or "all", "all"]))
        .order_by(EscalationPolicyRecord.country_code.desc(), EscalationPolicyRecord.channel.desc(), EscalationPolicyRecord.id.asc())
        .all()
    )
    policies: list[EscalationPolicy] = []
    seen: set[str] = set()
    for row in rows:
        if row.risk_key in seen:
            continue
        seen.add(row.risk_key)
        try:
            action = EscalationAction(row.action)
        except ValueError:
            action = EscalationAction.HANDOFF_OR_TICKET
        policies.append(EscalationPolicy(
            risk_key=row.risk_key,
            patterns=[str(item) for item in (row.trigger_patterns_json or [])],
            action=action,
            max_ai_attempts=row.max_ai_attempts,
            forbidden_commitments=[str(item) for item in (row.forbidden_commitments_json or [])],
            enabled=row.enabled,
        ))
    return policies


def resolve_tool_execution_policy(db: Session, *, tool_name: str, country_code: str | None, channel: str | None) -> ToolExecutionPolicy | None:
    row = _best_scoped_row(
        db,
        ToolExecutionPolicyRecord,
        country_code=country_code,
        channel=channel,
        extra_filters=[ToolExecutionPolicyRecord.tool_name == tool_name],
    )
    if not row:
        return None
    return ToolExecutionPolicy(
        tool_name=row.tool_name,
        enabled=row.enabled,
        ai_auto_executable=row.ai_auto_executable,
        risk_level=row.risk_level,
        requires_tracking_number=row.requires_tracking_number,
        requires_contact=row.requires_contact,
        requires_customer_confirmation=row.requires_customer_confirmation,
        requires_human_confirmation=row.requires_human_confirmation,
        allowed_channels=set(str(item) for item in (row.allowed_channels_json or [])),
        allowed_countries=set(str(item) for item in (row.allowed_countries_json or [])),
    )


def resolve_whatsapp_routing_rule(db: Session, *, country_code: str | None, issue_type: str | None, channel: str = "whatsapp") -> WhatsAppRoutingRuleRecord | None:
    return (
        db.query(WhatsAppRoutingRuleRecord)
        .filter(WhatsAppRoutingRuleRecord.enabled.is_(True))
        .filter(WhatsAppRoutingRuleRecord.country_code == (country_code or "GLOBAL"))
        .filter(WhatsAppRoutingRuleRecord.issue_type == (issue_type or "general"))
        .filter(WhatsAppRoutingRuleRecord.channel == channel)
        .order_by(WhatsAppRoutingRuleRecord.priority.asc(), WhatsAppRoutingRuleRecord.id.asc())
        .first()
    )


def audit_runtime_decision(
    db: Session,
    *,
    decision: RuntimeDecision,
    evaluation: RuntimeDecisionEvaluation,
    case_context: CaseContext | None = None,
    tenant_id: str = "default",
    channel: str | None = None,
    country_code: str | None = None,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
) -> RuntimeDecisionAuditRecord:
    """Persist one RuntimeDecisionAudit only after final-boundary sanitization."""

    violations = sanitize_audit_payload([asdict(item) for item in evaluation.violations])
    warnings = sanitize_audit_payload(list(evaluation.warnings))
    decision_payload = sanitize_audit_payload(_decision_json(decision))
    context_payload = sanitize_audit_payload(case_context.as_dict()) if case_context else None

    row = RuntimeDecisionAuditRecord(
        tenant_id=safe_audit_label(tenant_id, fallback="default", max_length=80),
        channel=safe_audit_label(channel, fallback="unknown", max_length=40) if channel else None,
        country_code=safe_audit_label(country_code, fallback="GLOBAL", max_length=16) if country_code else None,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        business_reply_type=safe_audit_label(decision.business_reply_type, fallback="unknown_reply_type", max_length=120),
        next_action=safe_audit_label(decision.next_action, fallback="unknown_action", max_length=120),
        risk_level=safe_audit_label(decision.risk_level, fallback="unknown", max_length=40),
        allowed=bool(evaluation.allowed),
        violations_json=violations if isinstance(violations, list) else [violations],
        warnings_json=warnings if isinstance(warnings, list) else [warnings],
        decision_json=decision_payload if isinstance(decision_payload, dict) else {"payload": decision_payload},
        case_context_json=context_payload if isinstance(context_payload, dict) or context_payload is None else {"payload": context_payload},
    )
    db.add(row)
    db.flush()
    return row


def _decision_json(decision: RuntimeDecision) -> dict[str, Any]:
    return {
        "business_reply_type": str(decision.business_reply_type),
        "next_action": str(decision.next_action),
        "risk_level": decision.risk_level,
        "handoff_required": decision.handoff_required,
        "ticket_required": decision.ticket_required,
        "routing_required": decision.routing_required,
        "evidence_sources": [
            {
                "evidence_type": str(item.evidence_type),
                "source_id": item.source_id,
                "label": item.label,
                "summary": item.safe_summary(),
                "confidence": item.confidence,
                "customer_visible": item.customer_visible,
                "verified": item.verified,
                "current_status": item.current_status,
                "created_at": item.created_at,
            }
            for item in decision.evidence_sources
        ],
        "tool_actions": [
            {
                "tool_name": item.tool_name,
                "arguments": item.arguments,
                "requires_confirmation": item.requires_confirmation,
                "executed": item.executed,
                "result_source_id": item.result_source_id,
            }
            for item in decision.tool_actions
        ],
        "audit_reasons": list(decision.audit_reasons),
    }


def _best_scoped_row(db: Session, model, *, country_code: str | None, channel: str | None, extra_filters: list[Any]):
    candidates = (
        db.query(model)
        .filter(*extra_filters)
        .filter(model.country_code.in_([country_code or "GLOBAL", "GLOBAL"]))
        .filter(model.channel.in_([channel or "all", "all"]))
        .all()
    )
    if not candidates:
        return None

    def score(row) -> tuple[int, int]:
        return (1 if row.country_code == (country_code or "GLOBAL") else 0, 1 if row.channel == (channel or "all") else 0)

    return max(candidates, key=score)


def _weekly_hours(value: dict | None) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for day, windows in (value or {}).items():
        parsed = []
        for window in windows or []:
            if isinstance(window, (list, tuple)) and len(window) == 2:
                parsed.append((str(window[0]), str(window[1])))
        if parsed:
            result[str(day).lower()[:3]] = parsed
    return result


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
