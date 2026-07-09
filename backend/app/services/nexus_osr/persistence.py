from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models_osr import (
    CaseContextRecord,
    EscalationPolicyRecord,
    HumanHoursPolicyRecord,
    RuntimeDecisionAuditRecord,
    ToolExecutionPolicyRecord,
    WhatsAppRoutingRuleRecord,
)
from .case_context import CaseContext, CaseContextStatus, ContactMethod
from .policies import EscalationAction, EscalationPolicy, HumanHoursPolicy, ToolExecutionPolicy
from .runtime_decision_contract import RuntimeDecision, RuntimeDecisionEvaluation


def load_case_context(db: Session, *, conversation_id: int | None = None, ticket_id: int | None = None) -> CaseContext | None:
    query = db.query(CaseContextRecord)
    if conversation_id is not None:
        query = query.filter(CaseContextRecord.conversation_id == conversation_id)
    if ticket_id is not None:
        query = query.filter(CaseContextRecord.ticket_id == ticket_id)
    row = query.order_by(CaseContextRecord.id.desc()).first()
    return record_to_case_context(row) if row else None


def save_case_context(db: Session, context: CaseContext, *, tenant_id: str = "default", expires_at: datetime | None = None) -> CaseContextRecord:
    row = None
    if context.conversation_id is not None or context.ticket_id is not None:
        query = db.query(CaseContextRecord)
        if context.conversation_id is not None:
            query = query.filter(CaseContextRecord.conversation_id == int(context.conversation_id))
        if context.ticket_id is not None:
            query = query.filter(CaseContextRecord.ticket_id == int(context.ticket_id))
        row = query.order_by(CaseContextRecord.id.desc()).first()
    if row is None:
        row = CaseContextRecord()
        db.add(row)
    row.tenant_id = tenant_id
    row.conversation_id = int(context.conversation_id) if context.conversation_id is not None and str(context.conversation_id).isdigit() else None
    row.ticket_id = int(context.ticket_id) if context.ticket_id is not None and str(context.ticket_id).isdigit() else None
    row.channel = context.channel
    row.country_code = context.country_code
    row.issue_type = context.issue_type
    row.status = str(context.status)
    row.safe_tracking_reference = context.safe_tracking_reference
    row.tracking_number_hash = context.tracking_number_hash
    row.contact_methods_json = [item.as_dict() for item in context.contact_methods]
    row.customer_claim_summary = context.customer_claim_summary
    row.last_mcp_fact_json = context.last_mcp_fact
    row.missing_info_json = list(context.missing_info)
    row.handoff_requested = context.handoff_requested
    row.ticket_created = context.ticket_created
    row.routed_group_key = context.routed_group_key
    row.ai_actions_taken_json = list(context.ai_actions_taken)
    row.agent_handover_summary = context.agent_handover_summary
    row.expires_at = expires_at
    row.closed_at = _parse_iso(context.closed_at)
    db.flush()
    return row


def record_to_case_context(row: CaseContextRecord) -> CaseContext:
    contacts = []
    for item in row.contact_methods_json or []:
        if isinstance(item, dict):
            contacts.append(ContactMethod(
                channel=str(item.get("channel") or "unknown"),
                value_redacted=str(item.get("value_redacted") or ""),
                source=str(item.get("source") or "unknown"),
                is_default=bool(item.get("is_default")),
            ))
    try:
        status = CaseContextStatus(row.status)
    except ValueError:
        status = CaseContextStatus.ACTIVE
    return CaseContext(
        conversation_id=row.conversation_id,
        ticket_id=row.ticket_id,
        channel=row.channel,
        country_code=row.country_code,
        issue_type=row.issue_type,
        status=status,
        safe_tracking_reference=row.safe_tracking_reference,
        tracking_number_hash=row.tracking_number_hash,
        contact_methods=contacts,
        customer_claim_summary=row.customer_claim_summary,
        last_mcp_fact=row.last_mcp_fact_json,
        missing_info=list(row.missing_info_json or []),
        handoff_requested=row.handoff_requested,
        ticket_created=row.ticket_created,
        routed_group_key=row.routed_group_key,
        ai_actions_taken=list(row.ai_actions_taken_json or []),
        agent_handover_summary=row.agent_handover_summary,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        closed_at=row.closed_at.isoformat() if row.closed_at else None,
    )


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
        extra_filters=[ToolExecutionPolicyRecord.tool_name == tool_name, ToolExecutionPolicyRecord.enabled.is_(True)],
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
    row = RuntimeDecisionAuditRecord(
        tenant_id=tenant_id,
        channel=channel,
        country_code=country_code,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        business_reply_type=str(decision.business_reply_type),
        next_action=str(decision.next_action),
        risk_level=decision.risk_level,
        allowed=evaluation.allowed,
        violations_json=[asdict(item) for item in evaluation.violations],
        warnings_json=list(evaluation.warnings),
        decision_json=_decision_json(decision),
        case_context_json=case_context.as_dict() if case_context else None,
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
