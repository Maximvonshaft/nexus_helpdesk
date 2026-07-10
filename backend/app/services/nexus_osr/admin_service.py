from __future__ import annotations

from collections import Counter
import hashlib
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...models_osr import (
    CaseContextRecord,
    EscalationPolicyRecord,
    HumanHoursPolicyRecord,
    RuntimeDecisionAuditRecord,
    ToolExecutionPolicyRecord,
    WhatsAppRoutingRuleRecord,
)
from ...services.webchat_ai_decision_runtime.tool_registry import get_tool_contract, safe_registry_summary
from ...utils.time import utc_now
from .case_context import redact_case_text

OSR_ADMIN_SCHEMA = "nexus.osr_admin.v1"
GLOBAL_CONFIGURATION_SCOPE = "global"

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_TRACKING_RE = re.compile(r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|\b(?:password|secret|api[_-]?key|credential)\s*[:=]\s*\S+)",
    re.I,
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|credential|password|secret|api[_-]?key|cookie|token|prompt|system_message|developer_message|raw(?:_|$)|"
    r"provider_(?:payload|request|response|body|group_id)|payload|tracking_number|phone|email|customer_reply|"
    r"tool_(?:args|arguments|result|results|payload)|(?:^|_)(?:arguments|credentials)(?:$|_)|destination_group_id|fallback_group_id)",
    re.I,
)
_SAFE_REFERENCE_KEYS = {
    "safe_tracking_reference",
    "tracking_number_hash",
    "tracking_number_hash_present",
    "sha256_prefix",
    "destination_group_id_hash",
    "destination_group_id_present",
    "destination_group_key",
    "fallback_group_id_hash",
    "fallback_group_id_present",
    "fallback_group_key",
    "tenant_id",
    "country_code",
    "channel",
    "issue_type",
    "status",
    "routed_group_key",
    "queue_key",
    "working_hours_key",
    "risk_key",
    "tool_name",
    "action",
    "fallback_action",
    "business_reply_type",
    "next_action",
    "risk_level",
    "audit_level",
    "missing_evidence",
    "blocked_reason",
    "evidence_type",
}
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")

POLICY_CONFIGS: dict[str, dict[str, Any]] = {
    "human_hours": {
        "model": HumanHoursPolicyRecord,
        "required": {"queue_key"},
        "fields": {
            "country_code", "channel", "queue_key", "timezone_name", "working_hours_json",
            "holiday_calendar_json", "handoff_enabled", "offline_message_template",
            "auto_ticket_when_offline", "customer_wait_timeout_seconds", "fallback_action", "enabled",
        },
        "filters": {"country_code", "channel", "queue_key", "enabled"},
    },
    "escalation": {
        "model": EscalationPolicyRecord,
        "required": {"risk_key"},
        "fields": {
            "risk_key", "country_code", "channel", "trigger_patterns_json", "semantic_intents_json",
            "max_ai_attempts", "action", "handoff_required", "ticket_required",
            "forbidden_commitments_json", "allowed_resolution_actions_json", "enabled",
        },
        "filters": {"risk_key", "country_code", "channel", "enabled"},
    },
    "tool_execution": {
        "model": ToolExecutionPolicyRecord,
        "required": {"tool_name"},
        "fields": {
            "tool_name", "country_code", "channel", "enabled", "ai_auto_executable", "risk_level",
            "requires_tracking_number", "requires_contact", "requires_customer_confirmation",
            "requires_human_confirmation", "allowed_channels_json", "allowed_countries_json",
            "customer_visible_success_template", "customer_visible_failure_template", "audit_level",
        },
        "filters": {"tool_name", "country_code", "channel", "enabled"},
    },
    "whatsapp_routing": {
        "model": WhatsAppRoutingRuleRecord,
        "required": {"country_code", "issue_type", "destination_group_id"},
        "fields": {
            "country_code", "issue_type", "channel", "destination_group_id", "fallback_group_id",
            "working_hours_key", "message_template", "priority", "enabled",
        },
        "filters": {"country_code", "issue_type", "channel", "enabled"},
    },
}


def normalize_tenant_id(value: Any) -> str:
    tenant_id = str(value or "default").strip()
    if not _TENANT_RE.fullmatch(tenant_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_tenant_scope")
    return tenant_id


def _hash(value: Any, *, length: int = 16) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()[:length]


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _sensitive_key(key: str) -> bool:
    return key.lower() not in _SAFE_REFERENCE_KEYS and bool(_SENSITIVE_KEY_RE.search(key))


def _redacted_marker(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {"redacted": True, "sha256_prefix": _hash(text), "present": value not in (None, "")}


def _clean_scalar(value: Any, *, limit: int = 240, key: str = "") -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if _sensitive_key(key):
        return _redacted_marker(text)
    text = _SECRET_VALUE_RE.sub("[redacted_secret]", text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    if key.lower() not in _SAFE_REFERENCE_KEYS:
        text = _TRACKING_RE.sub("[redacted_tracking]", text)
    if len(text) > limit:
        return {"redacted": True, "length": len(text), "sha256_prefix": _hash(text)}
    return text


def safe_json(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth > 5:
        return {"redacted": True, "type": type(value).__name__, "sha256_prefix": _hash(value)}
    if _sensitive_key(key):
        return _redacted_marker(value)
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:80]:
            item_key = str(raw_key)
            safe[item_key] = _redacted_marker(item) if _sensitive_key(item_key) else safe_json(item, key=item_key, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [safe_json(item, key=key, depth=depth + 1) for item in list(value)[:50]]
    return _clean_scalar(value, key=key)


def safe_group_reference(value: Any, *, prefix: str) -> dict[str, Any]:
    if value in (None, ""):
        return {
            f"{prefix}_group_id_present": False,
            f"{prefix}_group_id_hash": None,
            f"{prefix}_group_key": None,
        }
    digest = _hash(value)
    return {
        f"{prefix}_group_id_present": True,
        f"{prefix}_group_id_hash": digest,
        f"{prefix}_group_key": f"provider-group:{digest[:12]}",
    }


def _limit_offset(limit: int, offset: int) -> tuple[int, int]:
    return min(max(int(limit or 50), 1), 200), max(int(offset or 0), 0)


def _policy_config(policy_type: str) -> dict[str, Any]:
    try:
        return POLICY_CONFIGS[policy_type]
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="osr_policy_type_not_found") from exc


def _policy_type_for_row(row: Any) -> str:
    if isinstance(row, WhatsAppRoutingRuleRecord):
        return "whatsapp_routing"
    if isinstance(row, HumanHoursPolicyRecord):
        return "human_hours"
    if isinstance(row, EscalationPolicyRecord):
        return "escalation"
    if isinstance(row, ToolExecutionPolicyRecord):
        return "tool_execution"
    return "unknown"


def policy_record_to_dict(row: Any, *, policy_type: str | None = None) -> dict[str, Any]:
    resolved_type = policy_type or _policy_type_for_row(row)
    data: dict[str, Any] = {
        "schema": OSR_ADMIN_SCHEMA,
        "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
        "policy_type": resolved_type,
        "id": row.id,
    }
    for column in row.__table__.columns:
        name = column.name
        if name == "id":
            continue
        value = getattr(row, name)
        if resolved_type == "whatsapp_routing" and name == "destination_group_id":
            data.update(safe_group_reference(value, prefix="destination"))
            continue
        if resolved_type == "whatsapp_routing" and name == "fallback_group_id":
            data.update(safe_group_reference(value, prefix="fallback"))
            continue
        data[name] = _iso(value) if name.endswith("_at") else safe_json(value, key=name)
    return data


def list_policy_records(
    db: Session,
    policy_type: str,
    *,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    config = _policy_config(policy_type)
    model = config["model"]
    query = db.query(model)
    for key, value in (filters or {}).items():
        if value is None or value == "" or key not in config["filters"]:
            continue
        column = getattr(model, key)
        query = query.filter(column.is_(value) if isinstance(value, bool) else column == value)
    total = query.count()
    capped_limit, capped_offset = _limit_offset(limit, offset)
    rows = query.order_by(model.id.desc()).offset(capped_offset).limit(capped_limit).all()
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
        "items": [policy_record_to_dict(row, policy_type=policy_type) for row in rows],
        "total": total,
        "limit": capped_limit,
        "offset": capped_offset,
    }


def _get_policy_row(db: Session, policy_type: str, record_id: int) -> Any:
    model = _policy_config(policy_type)["model"]
    row = db.get(model, record_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="osr_policy_record_not_found")
    return row


def get_policy_record(db: Session, policy_type: str, record_id: int) -> dict[str, Any]:
    return policy_record_to_dict(_get_policy_row(db, policy_type, record_id), policy_type=policy_type)


def _validate_payload_fields(config: dict[str, Any], payload: dict[str, Any], *, create: bool) -> None:
    unknown = sorted(set(payload) - config["fields"])
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "unknown_fields", "fields": unknown})
    if create:
        missing = sorted(field for field in config["required"] if payload.get(field) in (None, ""))
        if missing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "required_fields_missing", "fields": missing})


def _apply_payload(row: Any, payload: dict[str, Any], *, allowed_fields: set[str]) -> None:
    for key, value in payload.items():
        if key in allowed_fields:
            setattr(row, key, value)


def create_policy_record(db: Session, policy_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = _policy_config(policy_type)
    _validate_payload_fields(config, payload, create=True)
    row = config["model"]()
    _apply_payload(row, payload, allowed_fields=config["fields"])
    db.add(row)
    db.flush()
    return policy_record_to_dict(row, policy_type=policy_type)


def update_policy_record(db: Session, policy_type: str, record_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    config = _policy_config(policy_type)
    _validate_payload_fields(config, payload, create=False)
    row = _get_policy_row(db, policy_type, record_id)
    _apply_payload(row, payload, allowed_fields=config["fields"])
    row.updated_at = utc_now()
    db.flush()
    return policy_record_to_dict(row, policy_type=policy_type)


def delete_policy_record(db: Session, policy_type: str, record_id: int) -> dict[str, Any]:
    row = _get_policy_row(db, policy_type, record_id)
    if hasattr(row, "enabled"):
        row.enabled = False
        row.updated_at = utc_now()
        db.flush()
        return {
            "schema": OSR_ADMIN_SCHEMA,
            "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
            "ok": True,
            "deleted": False,
            "disabled": True,
            "record": policy_record_to_dict(row, policy_type=policy_type),
        }
    db.delete(row)
    db.flush()
    return {"schema": OSR_ADMIN_SCHEMA, "ok": True, "deleted": True, "disabled": False}


def _scope_score(row: Any, *, country_code: str | None, channel: str | None) -> tuple[int, int]:
    return (
        1 if str(getattr(row, "country_code", "") or "").upper() == str(country_code or "GLOBAL").upper() else 0,
        1 if str(getattr(row, "channel", "") or "").lower() == str(channel or "all").lower() else 0,
    )


def _scope_reason(row: Any, *, country_code: str | None, channel: str | None) -> list[str]:
    row_country = str(getattr(row, "country_code", "") or "GLOBAL").upper()
    row_channel = str(getattr(row, "channel", "") or "all").lower()
    requested_country = str(country_code or "GLOBAL").upper()
    requested_channel = str(channel or "all").lower()
    return [
        "exact_country" if row_country == requested_country else "global_country_fallback",
        "exact_channel" if row_channel == requested_channel else "all_channel_fallback",
    ]


def _scoped_candidates(db: Session, model: Any, *, country_code: str | None, channel: str | None, filters: list[Any]) -> list[Any]:
    return (
        db.query(model)
        .filter(*filters)
        .filter(model.country_code.in_([country_code or "GLOBAL", "GLOBAL"]))
        .filter(model.channel.in_([channel or "all", "all"]))
        .all()
    )


def preview_human_hours_policy(db: Session, *, country_code: str | None, channel: str | None, queue_key: str) -> dict[str, Any]:
    candidates = _scoped_candidates(
        db,
        HumanHoursPolicyRecord,
        country_code=country_code,
        channel=channel,
        filters=[HumanHoursPolicyRecord.queue_key == queue_key, HumanHoursPolicyRecord.enabled.is_(True)],
    )
    candidates = sorted(candidates, key=lambda row: _scope_score(row, country_code=country_code, channel=channel), reverse=True)
    matched = candidates[0] if candidates else None
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
        "preview_type": "human_hours_policy_effective_preview",
        "matched": bool(matched),
        "matched_policy": policy_record_to_dict(matched, policy_type="human_hours") if matched else None,
        "why_matched": [*_scope_reason(matched, country_code=country_code, channel=channel), "queue_key_match"] if matched else ["no_enabled_policy_for_scope"],
        "candidate_count": len(candidates),
        "candidates": [policy_record_to_dict(row, policy_type="human_hours") for row in candidates[:10]],
    }


def preview_escalation_policy(
    db: Session,
    *,
    country_code: str | None,
    channel: str | None,
    message: str | None,
    ai_attempt_count: int = 0,
) -> dict[str, Any]:
    candidates = _scoped_candidates(
        db,
        EscalationPolicyRecord,
        country_code=country_code,
        channel=channel,
        filters=[EscalationPolicyRecord.enabled.is_(True)],
    )
    candidates = sorted(candidates, key=lambda row: _scope_score(row, country_code=country_code, channel=channel), reverse=True)
    text = str(message or "")[:2000]
    matched = None
    matched_pattern = None
    for row in candidates:
        for pattern in row.trigger_patterns_json or []:
            if re.search(str(pattern), text, re.IGNORECASE):
                matched = row
                matched_pattern = str(pattern)
                break
        if matched:
            break
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
        "preview_type": "escalation_policy_resolution_preview",
        "matched": bool(matched),
        "matched_policy": policy_record_to_dict(matched, policy_type="escalation") if matched else None,
        "why_matched": [*_scope_reason(matched, country_code=country_code, channel=channel), "regex_pattern_match", f"ai_attempt_count={ai_attempt_count}"] if matched else ["no_regex_match"],
        "matched_pattern_hash": _hash(matched_pattern) if matched_pattern else None,
        "message_echoed": False,
        "candidate_count": len(candidates),
        "candidates": [policy_record_to_dict(row, policy_type="escalation") for row in candidates[:10]],
    }


def preview_tool_execution_policy(
    db: Session,
    *,
    tool_name: str,
    country_code: str | None,
    channel: str | None,
    has_tracking_number: bool = False,
    has_contact: bool = False,
) -> dict[str, Any]:
    contract = get_tool_contract(tool_name)
    if contract is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_name_not_registered")
    candidates = _scoped_candidates(
        db,
        ToolExecutionPolicyRecord,
        country_code=country_code,
        channel=channel,
        filters=[ToolExecutionPolicyRecord.tool_name == contract.name, ToolExecutionPolicyRecord.enabled.is_(True)],
    )
    candidates = sorted(candidates, key=lambda row: _scope_score(row, country_code=country_code, channel=channel), reverse=True)
    matched = candidates[0] if candidates else None
    missing: list[str] = []
    allowed = False
    decision_reason = "no_enabled_policy_for_scope"
    if matched:
        if not matched.ai_auto_executable:
            decision_reason = "tool_not_ai_auto_executable"
        elif matched.allowed_channels_json and (channel or "") not in set(matched.allowed_channels_json or []):
            decision_reason = "channel_not_allowed"
        elif matched.allowed_countries_json and (country_code or "") not in set(matched.allowed_countries_json or []):
            decision_reason = "country_not_allowed"
        else:
            if matched.requires_tracking_number and not has_tracking_number:
                missing.append("tracking_number")
            if matched.requires_contact and not has_contact:
                missing.append("contact_method")
            if missing:
                decision_reason = "missing_required_context"
            else:
                allowed = True
                decision_reason = "allowed"
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
        "preview_type": "tool_execution_policy_effective_preview",
        "registered_tool": {
            "name": contract.name,
            "classification": contract.classification,
            "risk_level": contract.risk_level,
            "confirmation_required": contract.confirmation_required,
            "controlled_action_required": contract.controlled_action_required,
            "allowed_auto_execution_mode": contract.allowed_auto_execution_mode,
        },
        "matched": bool(matched),
        "matched_policy": policy_record_to_dict(matched, policy_type="tool_execution") if matched else None,
        "allowed": allowed,
        "decision_reason": decision_reason,
        "missing_requirements": missing,
        "why_matched": [*_scope_reason(matched, country_code=country_code, channel=channel), "tool_name_match"] if matched else ["no_policy_for_registered_tool"],
        "candidate_count": len(candidates),
        "registered_tools": safe_registry_summary(),
    }


def preview_whatsapp_routing_rule(db: Session, *, country_code: str | None, issue_type: str | None, channel: str = "whatsapp") -> dict[str, Any]:
    candidates = (
        db.query(WhatsAppRoutingRuleRecord)
        .filter(WhatsAppRoutingRuleRecord.enabled.is_(True))
        .filter(WhatsAppRoutingRuleRecord.country_code == (country_code or "GLOBAL"))
        .filter(WhatsAppRoutingRuleRecord.issue_type == (issue_type or "general"))
        .filter(WhatsAppRoutingRuleRecord.channel == (channel or "whatsapp"))
        .order_by(WhatsAppRoutingRuleRecord.priority.asc(), WhatsAppRoutingRuleRecord.id.asc())
        .all()
    )
    matched = candidates[0] if candidates else None
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
        "preview_type": "whatsapp_rule_resolution_preview",
        "routing_configured": bool(matched),
        "matched_rule": policy_record_to_dict(matched, policy_type="whatsapp_routing") if matched else None,
        "why_matched": ["exact_country", "exact_issue_type", "exact_channel", "lowest_priority"] if matched else ["routing_rule_missing"],
        "candidate_count": len(candidates),
        "candidates": [policy_record_to_dict(row, policy_type="whatsapp_routing") for row in candidates[:10]],
    }


def case_context_to_safe_dict(row: CaseContextRecord, *, detail: bool = False) -> dict[str, Any]:
    data = {
        "schema": OSR_ADMIN_SCHEMA,
        "tenant_id": _clean_scalar(row.tenant_id, key="tenant_id"),
        "id": row.id,
        "conversation_id": row.conversation_id,
        "ticket_id": row.ticket_id,
        "channel": _clean_scalar(row.channel, key="channel"),
        "country_code": _clean_scalar(row.country_code, key="country_code"),
        "issue_type": _clean_scalar(row.issue_type, key="issue_type"),
        "status": _clean_scalar(row.status, key="status"),
        "safe_tracking_reference": _clean_scalar(row.safe_tracking_reference, key="safe_tracking_reference"),
        "tracking_number_hash_present": bool(row.tracking_number_hash),
        "contact_methods": safe_json(row.contact_methods_json or [], key="contact_methods"),
        "customer_claim_summary": _clean_scalar(row.customer_claim_summary, key="customer_claim_summary", limit=300),
        "missing_info": safe_json(row.missing_info_json or [], key="missing_info"),
        "handoff_requested": bool(row.handoff_requested),
        "ticket_created": bool(row.ticket_created),
        "routed_group_key": _clean_scalar(row.routed_group_key, key="routed_group_key"),
        "ai_actions_taken": safe_json(row.ai_actions_taken_json or [], key="ai_actions_taken"),
        "agent_handover_summary": _clean_scalar(row.agent_handover_summary, key="agent_handover_summary", limit=300),
        "expires_at": _iso(row.expires_at),
        "closed_at": _iso(row.closed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if detail:
        data["last_mcp_fact"] = safe_json(row.last_mcp_fact_json or {}, key="last_mcp_fact")
    return data


def list_case_contexts(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    tenant_id = normalize_tenant_id(tenant_id)
    query = db.query(CaseContextRecord).filter(CaseContextRecord.tenant_id == tenant_id)
    if conversation_id is not None:
        query = query.filter(CaseContextRecord.conversation_id == conversation_id)
    if ticket_id is not None:
        query = query.filter(CaseContextRecord.ticket_id == ticket_id)
    if status_filter:
        query = query.filter(CaseContextRecord.status == status_filter)
    total = query.count()
    capped_limit, capped_offset = _limit_offset(limit, offset)
    rows = query.order_by(CaseContextRecord.id.desc()).offset(capped_offset).limit(capped_limit).all()
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "tenant_id": tenant_id,
        "items": [case_context_to_safe_dict(row) for row in rows],
        "total": total,
        "limit": capped_limit,
        "offset": capped_offset,
    }


def _get_case_context_row(db: Session, *, tenant_id: str, context_id: int) -> CaseContextRecord:
    tenant_id = normalize_tenant_id(tenant_id)
    row = (
        db.query(CaseContextRecord)
        .filter(CaseContextRecord.id == context_id, CaseContextRecord.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="osr_case_context_not_found")
    return row


def get_case_context(db: Session, context_id: int, *, tenant_id: str) -> dict[str, Any]:
    return case_context_to_safe_dict(_get_case_context_row(db, tenant_id=tenant_id, context_id=context_id), detail=True)


def update_case_context_safe_fields(db: Session, context_id: int, payload: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    allowed = {"status", "issue_type", "routed_group_key", "handoff_requested", "agent_handover_summary", "missing_info_json"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "unsafe_or_unknown_fields", "fields": unknown})
    row = _get_case_context_row(db, tenant_id=tenant_id, context_id=context_id)
    for key, value in payload.items():
        if key == "status" and hasattr(value, "value"):
            value = value.value
        if key == "agent_handover_summary":
            value = redact_case_text(value, limit=600) or None
        elif key == "missing_info_json":
            value = [str(item)[:120] for item in (value or [])[:30]]
        setattr(row, key, value)
    row.updated_at = utc_now()
    db.flush()
    return case_context_to_safe_dict(row, detail=True)


def _decision_summary(decision: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(decision, dict):
        return {}
    evidence: list[dict[str, Any]] = []
    for item in decision.get("evidence_sources") or []:
        if not isinstance(item, dict):
            continue
        evidence.append({
            "evidence_type": _clean_scalar(item.get("evidence_type"), key="evidence_type"),
            "source_id_hash": _hash(item.get("source_id")) if item.get("source_id") not in (None, "") else None,
            "source_id_present": item.get("source_id") not in (None, ""),
            "label": _clean_scalar(item.get("label"), key="label"),
            "summary": _clean_scalar(item.get("summary"), key="evidence_summary", limit=240),
            "confidence": item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
            "verified": bool(item.get("verified")),
            "current_status": bool(item.get("current_status")),
            "customer_visible": bool(item.get("customer_visible")),
        })
    tools: list[dict[str, Any]] = []
    for item in decision.get("tool_actions") or []:
        if not isinstance(item, dict):
            continue
        tools.append({
            "tool_name": _clean_scalar(item.get("tool_name"), key="tool_name"),
            "requires_confirmation": bool(item.get("requires_confirmation")),
            "executed": bool(item.get("executed")),
            "result_source_present": item.get("result_source_id") not in (None, ""),
            "result_source_hash": _hash(item.get("result_source_id")) if item.get("result_source_id") not in (None, "") else None,
        })
    return {
        "business_reply_type": _clean_scalar(decision.get("business_reply_type"), key="business_reply_type"),
        "next_action": _clean_scalar(decision.get("next_action"), key="next_action"),
        "risk_level": _clean_scalar(decision.get("risk_level"), key="risk_level"),
        "handoff_required": bool(decision.get("handoff_required")),
        "ticket_required": bool(decision.get("ticket_required")),
        "routing_required": bool(decision.get("routing_required")),
        "evidence_sources": evidence[:20],
        "tool_actions": tools[:20],
        "audit_reasons": safe_json(decision.get("audit_reasons") or [], key="audit_reasons"),
    }


def runtime_audit_to_safe_dict(row: RuntimeDecisionAuditRecord, *, detail: bool = False) -> dict[str, Any]:
    decision = row.decision_json or {}
    data = {
        "schema": OSR_ADMIN_SCHEMA,
        "tenant_id": _clean_scalar(row.tenant_id, key="tenant_id"),
        "id": row.id,
        "channel": _clean_scalar(row.channel, key="channel"),
        "country_code": _clean_scalar(row.country_code, key="country_code"),
        "conversation_id": row.conversation_id,
        "ticket_id": row.ticket_id,
        "business_reply_type": _clean_scalar(row.business_reply_type, key="business_reply_type"),
        "next_action": _clean_scalar(row.next_action, key="next_action"),
        "risk_level": _clean_scalar(row.risk_level, key="risk_level"),
        "allowed": bool(row.allowed),
        "violation_count": len(row.violations_json or []),
        "warning_count": len(row.warnings_json or []),
        "violations": safe_json(row.violations_json or [], key="violations"),
        "warnings": safe_json(row.warnings_json or [], key="warnings"),
        "decision_summary": _decision_summary(decision),
        "created_at": _iso(row.created_at),
    }
    if detail:
        data["case_context_snapshot"] = safe_json(row.case_context_json or {}, key="case_context_snapshot")
    return data


def list_runtime_audits(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    allowed: bool | None = None,
    channel: str | None = None,
    country_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    tenant_id = normalize_tenant_id(tenant_id)
    query = db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.tenant_id == tenant_id)
    if conversation_id is not None:
        query = query.filter(RuntimeDecisionAuditRecord.conversation_id == conversation_id)
    if ticket_id is not None:
        query = query.filter(RuntimeDecisionAuditRecord.ticket_id == ticket_id)
    if allowed is not None:
        query = query.filter(RuntimeDecisionAuditRecord.allowed.is_(allowed))
    if channel:
        query = query.filter(RuntimeDecisionAuditRecord.channel == channel)
    if country_code:
        query = query.filter(RuntimeDecisionAuditRecord.country_code == country_code)
    total = query.count()
    capped_limit, capped_offset = _limit_offset(limit, offset)
    rows = query.order_by(RuntimeDecisionAuditRecord.id.desc()).offset(capped_offset).limit(capped_limit).all()
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "tenant_id": tenant_id,
        "items": [runtime_audit_to_safe_dict(row) for row in rows],
        "total": total,
        "limit": capped_limit,
        "offset": capped_offset,
    }


def get_runtime_audit(db: Session, audit_id: int, *, tenant_id: str) -> dict[str, Any]:
    tenant_id = normalize_tenant_id(tenant_id)
    row = (
        db.query(RuntimeDecisionAuditRecord)
        .filter(RuntimeDecisionAuditRecord.id == audit_id, RuntimeDecisionAuditRecord.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="osr_runtime_audit_not_found")
    return runtime_audit_to_safe_dict(row, detail=True)


def _missing_evidence_from(row: RuntimeDecisionAuditRecord | None, summary: dict[str, Any]) -> list[str]:
    if not row:
        return ["runtime_decision_audit_missing"]
    missing: list[str] = []
    for item in row.violations_json or []:
        text = str(item if not isinstance(item, dict) else item.get("code") or item.get("reason") or item.get("type") or "unknown")
        lowered = text.lower()
        if any(marker in lowered for marker in ("missing", "no_source", "without_tool", "tool_fact", "evidence")):
            missing.append(str(_clean_scalar(text, key="missing_evidence", limit=160) or "missing_evidence"))
    if not summary.get("evidence_sources") and not row.allowed:
        missing.append("verified_evidence_source_missing")
    return list(dict.fromkeys(missing))[:20]


def _tenant_group_counts(db: Session, model: Any, column_name: str, *, tenant_id: str, limit: int) -> list[dict[str, Any]]:
    column = getattr(model, column_name)
    rows = (
        db.query(column, func.count(model.id))
        .filter(model.tenant_id == tenant_id)
        .group_by(column)
        .order_by(func.count(model.id).desc())
        .limit(max(1, min(limit, 50)))
        .all()
    )
    return [{"key": _clean_scalar(key, key=column_name) or "unknown", "count": int(count)} for key, count in rows]


def _blocked_reason_counts(rows: list[RuntimeDecisionAuditRecord], *, limit: int) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for row in rows:
        for item in row.violations_json or []:
            if isinstance(item, dict):
                key = item.get("code") or item.get("reason") or item.get("type") or "unknown"
            else:
                key = str(item or "unknown")
            counter[str(_clean_scalar(key, key="blocked_reason", limit=120) or "unknown")] += 1
    return [{"reason": reason, "count": count} for reason, count in counter.most_common(max(1, min(limit, 50)))]


def _missing_routing_counts(rows: list[CaseContextRecord], *, limit: int) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        counter[(row.country_code or "GLOBAL", row.channel or "unknown", row.issue_type or "unknown")] += 1
    return [
        {
            "country_code": _clean_scalar(country, key="country_code"),
            "channel": _clean_scalar(channel, key="channel"),
            "issue_type": _clean_scalar(issue, key="issue_type"),
            "count": count,
        }
        for (country, channel, issue), count in counter.most_common(max(1, min(limit, 50)))
    ]


def control_tower_summary(db: Session, *, tenant_id: str, top_n: int = 10) -> dict[str, Any]:
    tenant_id = normalize_tenant_id(tenant_id)
    top_n = max(1, min(int(top_n or 10), 50))
    audit_query = db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.tenant_id == tenant_id)
    context_query = db.query(CaseContextRecord).filter(CaseContextRecord.tenant_id == tenant_id)
    decision_count = audit_query.count()
    allowed_count = audit_query.filter(RuntimeDecisionAuditRecord.allowed.is_(True)).count()
    blocked_query = audit_query.filter(RuntimeDecisionAuditRecord.allowed.is_(False))
    blocked_count = blocked_query.count()
    escalation_count = audit_query.filter(
        or_(
            RuntimeDecisionAuditRecord.next_action.ilike("%handoff%"),
            RuntimeDecisionAuditRecord.next_action.ilike("%escalat%"),
            RuntimeDecisionAuditRecord.business_reply_type.ilike("%handoff%"),
        )
    ).count()
    missing_routing_rows = context_query.filter(
        CaseContextRecord.handoff_requested.is_(True),
        CaseContextRecord.routed_group_key.is_(None),
    ).all()
    blocked_rows = blocked_query.order_by(RuntimeDecisionAuditRecord.id.desc()).limit(1000).all()
    return {
        "schema": OSR_ADMIN_SCHEMA,
        "tenant_id": tenant_id,
        "runtime_decisions": {
            "total": decision_count,
            "allowed": allowed_count,
            "blocked": blocked_count,
            "by_country": _tenant_group_counts(db, RuntimeDecisionAuditRecord, "country_code", tenant_id=tenant_id, limit=top_n),
            "by_channel": _tenant_group_counts(db, RuntimeDecisionAuditRecord, "channel", tenant_id=tenant_id, limit=top_n),
            "by_business_reply_type": _tenant_group_counts(db, RuntimeDecisionAuditRecord, "business_reply_type", tenant_id=tenant_id, limit=top_n),
        },
        "case_contexts": {
            "ticket_created": context_query.filter(CaseContextRecord.ticket_created.is_(True)).count(),
            "handoff_requested": context_query.filter(CaseContextRecord.handoff_requested.is_(True)).count(),
            "by_country": _tenant_group_counts(db, CaseContextRecord, "country_code", tenant_id=tenant_id, limit=top_n),
            "by_channel": _tenant_group_counts(db, CaseContextRecord, "channel", tenant_id=tenant_id, limit=top_n),
            "by_issue_type": _tenant_group_counts(db, CaseContextRecord, "issue_type", tenant_id=tenant_id, limit=top_n),
        },
        "escalations": {"total": escalation_count},
        "routing": {
            "configuration_scope": GLOBAL_CONFIGURATION_SCOPE,
            "configured_rules": db.query(WhatsAppRoutingRuleRecord).filter(WhatsAppRoutingRuleRecord.enabled.is_(True)).count(),
            "missing_case_contexts": len(missing_routing_rows),
            "missing_routing_top": _missing_routing_counts(missing_routing_rows, limit=top_n),
        },
        "blocked_reason_top": _blocked_reason_counts(blocked_rows, limit=top_n),
    }
