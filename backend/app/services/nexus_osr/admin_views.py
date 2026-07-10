from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...models_osr import CaseContextRecord, RuntimeDecisionAuditRecord
from .admin_service import (
    _missing_evidence_from,
    case_context_to_safe_dict,
    normalize_tenant_id,
    runtime_audit_to_safe_dict,
    safe_group_reference,
    safe_json,
)

OSR_DEBUG_SCHEMA = "nexus.osr_debug.v1"


def hide_provider_group_ids(value: Any) -> Any:
    """Backward-compatible recursive redactor for provider group identifiers."""
    if isinstance(value, list):
        return [hide_provider_group_ids(item) for item in value]
    if not isinstance(value, dict):
        return value
    output: dict[str, Any] = {}
    for key, item in value.items():
        if key == "destination_group_id":
            output.update(safe_group_reference(item, prefix="destination"))
            continue
        if key == "fallback_group_id":
            output.update(safe_group_reference(item, prefix="fallback"))
            continue
        output[key] = hide_provider_group_ids(item)
    return output


def _tool_execution_summary(decision_summary: dict[str, Any]) -> dict[str, Any]:
    actions = decision_summary.get("tool_actions") if isinstance(decision_summary.get("tool_actions"), list) else []
    return {
        "actions": actions[:20],
        "executed_count": sum(1 for action in actions if isinstance(action, dict) and action.get("executed")),
        "requires_confirmation_count": sum(
            1 for action in actions if isinstance(action, dict) and action.get("requires_confirmation")
        ),
    }


def _operations_dispatch_summary(context: CaseContextRecord | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return {
        "routed": bool(context.routed_group_key),
        "routed_group_key": safe_json(context.routed_group_key, key="routed_group_key"),
        "handoff_requested": bool(context.handoff_requested),
        "ticket_created": bool(context.ticket_created),
    }


def _legacy_case_context_summary(context: CaseContextRecord | None) -> dict[str, Any]:
    if context is None:
        return {}
    return {
        "case_context_id": context.id,
        "status": safe_json(context.status, key="status"),
        "channel": safe_json(context.channel, key="channel"),
        "country_code": safe_json(context.country_code, key="country_code"),
        "issue_type": safe_json(context.issue_type, key="issue_type"),
        "safe_tracking_reference": safe_json(context.safe_tracking_reference, key="safe_tracking_reference"),
        "tracking_number_hash_present": bool(context.tracking_number_hash),
        "handoff_requested": bool(context.handoff_requested),
        "ticket_created": bool(context.ticket_created),
    }


def _policy_snapshot(audit: RuntimeDecisionAuditRecord | None, context: CaseContextRecord | None) -> dict[str, Any]:
    return {
        "country_code": safe_json((audit.country_code if audit else None) or (context.country_code if context else None), key="country_code"),
        "channel": safe_json((audit.channel if audit else None) or (context.channel if context else None), key="channel"),
        "issue_type": safe_json(context.issue_type if context else None, key="issue_type"),
        "business_reply_type": safe_json(audit.business_reply_type if audit else None, key="business_reply_type"),
        "next_action": safe_json(audit.next_action if audit else None, key="next_action"),
        "risk_level": safe_json(audit.risk_level if audit else None, key="risk_level"),
    }


def build_osr_debug_snapshot(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: int | None,
    ticket_id: int | None,
) -> dict[str, Any]:
    tenant_id = normalize_tenant_id(tenant_id)
    audit = None
    context = None
    if conversation_id is not None or ticket_id is not None:
        audit_query = db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.tenant_id == tenant_id)
        context_query = db.query(CaseContextRecord).filter(CaseContextRecord.tenant_id == tenant_id)
        audit_clauses = []
        context_clauses = []
        if conversation_id is not None:
            audit_clauses.append(RuntimeDecisionAuditRecord.conversation_id == conversation_id)
            context_clauses.append(CaseContextRecord.conversation_id == conversation_id)
        if ticket_id is not None:
            audit_clauses.append(RuntimeDecisionAuditRecord.ticket_id == ticket_id)
            context_clauses.append(CaseContextRecord.ticket_id == ticket_id)
        audit = audit_query.filter(or_(*audit_clauses)).order_by(RuntimeDecisionAuditRecord.id.desc()).first()
        context = context_query.filter(or_(*context_clauses)).order_by(CaseContextRecord.id.desc()).first()

    latest_runtime_audit = runtime_audit_to_safe_dict(audit, detail=True) if audit else None
    decision_summary = (latest_runtime_audit or {}).get("decision_summary") or {}
    case_snapshot = case_context_to_safe_dict(context, detail=True) if context else None
    return {
        "schema": OSR_DEBUG_SCHEMA,
        "tenant_id": tenant_id,
        "mode": "audit_only",
        # Compatibility fields retained from the existing WebChat osr_audit metadata
        # contract while the richer unified snapshot is adopted.
        "audit_id": audit.id if audit else None,
        "allowed": bool(audit.allowed) if audit else None,
        "business_reply_type": safe_json(audit.business_reply_type if audit else None, key="business_reply_type"),
        "next_action": safe_json(audit.next_action if audit else None, key="next_action"),
        "risk_level": safe_json(audit.risk_level if audit else None, key="risk_level"),
        "violation_codes": [
            safe_json(item.get("code"), key="violation_code")
            for item in (audit.violations_json or [])
            if isinstance(item, dict) and item.get("code")
        ][:20] if audit else [],
        "warning_count": len(audit.warnings_json or []) if audit else 0,
        "case_context": _legacy_case_context_summary(context),
        "reply_metadata_audit": {
            "osr_audit_present": audit is not None,
            "case_context_present": context is not None,
            "allowed": bool(audit.allowed) if audit else None,
            "status": "allowed" if audit and audit.allowed else "blocked" if audit else "missing",
            "business_reply_type": safe_json(audit.business_reply_type if audit else None, key="business_reply_type"),
            "next_action": safe_json(audit.next_action if audit else None, key="next_action"),
            "violation_count": len(audit.violations_json or []) if audit else 0,
            "warning_count": len(audit.warnings_json or []) if audit else 0,
        },
        "latest_runtime_audit": latest_runtime_audit,
        "case_context_snapshot": case_snapshot,
        "policy_snapshot": _policy_snapshot(audit, context),
        "tool_execution_summary": _tool_execution_summary(decision_summary),
        "operations_dispatch_summary": _operations_dispatch_summary(context),
        "evidence_sources": decision_summary.get("evidence_sources") or [],
        "missing_evidence": _missing_evidence_from(audit, decision_summary),
    }
