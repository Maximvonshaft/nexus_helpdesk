from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AdminAuditLog, IntegrationRequestLog, TicketEvent, TicketOutboundMessage
from ..services.permissions import ensure_can_manage_runtime
from ..services.provider_runtime_status import get_provider_runtime_status
from ..tool_models import ToolCallLog
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/provider-runtime", tags=["admin-provider-runtime"])

_ALLOWED_PRIMARY_PROVIDERS = {"codex_app_server", "openclaw_responses", "openai_responses"}
_ALLOWED_FALLBACK_PROVIDERS = {"openclaw_responses", "rule_engine", "openai_responses"}
_WEBCHAT_FAST_SCENARIO = "webchat_fast_reply"
_WEBCHAT_FAST_OUTPUT_CONTRACT = "speedaf_webchat_fast_reply_v1"
_SENSITIVE_JSON_KEYS = ("password", "secret", "token", "authorization", "credential", "api_key")


class WebchatFastRoutingUpdate(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=36)
    channel_key: str = Field(default="website", min_length=1, max_length=100)
    primary_provider: str = Field(default="codex_app_server", min_length=1, max_length=100)
    fallback_providers: list[str] = Field(default_factory=lambda: ["openclaw_responses", "rule_engine"], max_length=5)
    canary_percent: int = Field(default=0, ge=0, le=100)
    kill_switch: bool = False
    enabled: bool = True
    timeout_ms: int = Field(default=10000, ge=1000, le=30000)

    def validate_allowed(self) -> None:
        if self.primary_provider not in _ALLOWED_PRIMARY_PROVIDERS:
            raise ValueError("primary_provider_not_allowed")
        forbidden = [provider for provider in self.fallback_providers if provider not in _ALLOWED_FALLBACK_PROVIDERS]
        if forbidden:
            raise ValueError("fallback_provider_not_allowed")
        if self.primary_provider == "codex_app_server" and "openclaw_responses" not in self.fallback_providers:
            raise ValueError("codex_requires_openclaw_fallback")


def _value(value):
    return getattr(value, "value", value)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _parse_json(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _redact_json(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in _SENSITIVE_JSON_KEYS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value[:50]]
    return value


def _provider_runtime_audit_rows(db: Session, request_id: str) -> list[dict]:
    try:
        rows = db.execute(text("""
            SELECT id, tenant_id, provider, request_id, channel_key, session_id,
                   operation, status, safe_summary, error_code, elapsed_ms, created_at
            FROM provider_runtime_audit_logs
            WHERE request_id = :request_id
            ORDER BY created_at DESC
            LIMIT 50
        """), {"request_id": request_id}).mappings().all()
    except SQLAlchemyError:
        db.rollback()
        return []
    items = []
    for row in rows:
        safe_summary = _parse_json(row["safe_summary"]) or {}
        items.append({
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "provider": row["provider"],
            "request_id": row["request_id"],
            "channel_key": row["channel_key"],
            "session_id": row["session_id"],
            "operation": row["operation"],
            "status": row["status"],
            "safe_summary": safe_summary if isinstance(safe_summary, dict) else {},
            "error_code": row["error_code"],
            "elapsed_ms": row["elapsed_ms"],
            "created_at": _iso(row["created_at"]),
        })
    return items


def _admin_audit_rows(db: Session, request_id: str) -> list[dict]:
    rows = db.query(AdminAuditLog).filter(or_(
        AdminAuditLog.old_value_json.contains(request_id),
        AdminAuditLog.new_value_json.contains(request_id),
    )).order_by(AdminAuditLog.created_at.desc()).limit(50).all()
    return [{
        "id": row.id,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "actor_id": row.actor_id,
        "old_value": _redact_json(_parse_json(row.old_value_json)),
        "new_value": _redact_json(_parse_json(row.new_value_json)),
        "created_at": _iso(row.created_at),
    } for row in rows]


def _tool_call_rows(db: Session, request_id: str) -> list[dict]:
    rows = db.query(ToolCallLog).filter(ToolCallLog.request_id == request_id).order_by(ToolCallLog.created_at.desc()).limit(50).all()
    return [{
        "id": row.id,
        "tool_name": row.tool_name,
        "provider": row.provider,
        "tool_type": row.tool_type,
        "status": row.status,
        "error_code": row.error_code,
        "elapsed_ms": row.elapsed_ms,
        "timeout_ms": row.timeout_ms,
        "ticket_id": row.ticket_id,
        "conversation_id": row.conversation_id,
        "background_job_id": row.background_job_id,
        "redaction_applied": row.redaction_applied,
        "created_at": _iso(row.created_at),
    } for row in rows]


def _ticket_timeline_rows(db: Session, request_id: str) -> list[dict]:
    rows = db.query(TicketEvent).filter(or_(
        TicketEvent.payload_json.contains(request_id),
        TicketEvent.note.contains(request_id),
        TicketEvent.old_value.contains(request_id),
        TicketEvent.new_value.contains(request_id),
    )).order_by(TicketEvent.created_at.desc()).limit(50).all()
    return [{
        "id": row.id,
        "ticket_id": row.ticket_id,
        "actor_id": row.actor_id,
        "event_type": _value(row.event_type),
        "field_name": row.field_name,
        "note": row.note,
        "payload": _redact_json(_parse_json(row.payload_json)),
        "created_at": _iso(row.created_at),
    } for row in rows]


def _outbound_rows(db: Session, request_id: str) -> list[dict]:
    rows = db.query(TicketOutboundMessage).filter(or_(
        TicketOutboundMessage.provider_message_id == request_id,
        TicketOutboundMessage.failure_code == request_id,
        TicketOutboundMessage.error_message.contains(request_id),
        TicketOutboundMessage.failure_reason.contains(request_id),
    )).order_by(TicketOutboundMessage.created_at.desc()).limit(50).all()
    return [{
        "id": row.id,
        "message_id": f"outbound:{row.id}",
        "ticket_id": row.ticket_id,
        "channel": _value(row.channel),
        "status": _value(row.status),
        "provider_status": row.provider_status,
        "provider_message_id": row.provider_message_id,
        "failure_code": row.failure_code,
        "failure_reason": row.failure_reason,
        "retry_count": row.retry_count,
        "max_retries": row.max_retries,
        "retryable": bool(_value(row.status) in {"pending", "dead"} and row.retry_count < row.max_retries),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    } for row in rows]


def _integration_rows(db: Session, request_id: str) -> list[dict]:
    rows = db.query(IntegrationRequestLog).filter(or_(
        IntegrationRequestLog.idempotency_key == request_id,
        IntegrationRequestLog.response_json.contains(request_id),
    )).order_by(IntegrationRequestLog.created_at.desc()).limit(50).all()
    return [{
        "id": row.id,
        "client_id": row.client_id,
        "endpoint": row.endpoint,
        "method": row.method,
        "idempotency_key": row.idempotency_key,
        "status_code": row.status_code,
        "error_code": row.error_code,
        "retryable": bool(row.status_code in {408, 409, 425, 429} or (row.status_code is not None and row.status_code >= 500)),
        "created_at": _iso(row.created_at),
    } for row in rows]


def _request_trace_summary(sections: dict[str, list[dict]]) -> dict:
    error_codes = sorted({
        str(item.get("error_code") or item.get("failure_code"))
        for rows in sections.values()
        for item in rows
        if item.get("error_code") or item.get("failure_code")
    })
    retryable = any(bool(item.get("retryable")) for rows in sections.values() for item in rows)
    return {
        "provider_runtime_count": len(sections["provider_runtime"]),
        "admin_audit_count": len(sections["admin_audit"]),
        "tool_call_count": len(sections["tool_calls"]),
        "timeline_count": len(sections["ticket_timeline"]),
        "outbound_count": len(sections["outbound_messages"]),
        "integration_count": len(sections["integration_requests"]),
        "error_codes": error_codes,
        "retryable": retryable,
    }


@router.get("/status")
def provider_runtime_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    return get_provider_runtime_status(db)


@router.get("/audit/recent")
def provider_runtime_audit_recent(
    request_id: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    capped_limit = min(max(int(limit or 20), 1), 100)
    params = {"limit": capped_limit}
    where = ""
    if request_id:
        where = "WHERE request_id = :request_id"
        params["request_id"] = request_id.strip()
    rows = db.execute(text(f"""
        SELECT id, tenant_id, provider, request_id, channel_key, session_id,
               operation, status, safe_summary, error_code, elapsed_ms, created_at
        FROM provider_runtime_audit_logs
        {where}
        ORDER BY created_at DESC
        LIMIT :limit
    """), params).mappings().all()
    items = []
    for row in rows:
        safe_summary = row["safe_summary"]
        if isinstance(safe_summary, str):
            try:
                safe_summary = json.loads(safe_summary)
            except json.JSONDecodeError:
                safe_summary = {}
        items.append({
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "provider": row["provider"],
            "request_id": row["request_id"],
            "channel_key": row["channel_key"],
            "session_id": row["session_id"],
            "operation": row["operation"],
            "status": row["status"],
            "safe_summary": safe_summary if isinstance(safe_summary, dict) else {},
            "error_code": row["error_code"],
            "elapsed_ms": row["elapsed_ms"],
            "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        })
    return {"items": items, "total": len(items)}


@router.get("/request-trace/{request_id}")
def provider_runtime_request_trace(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    normalized = request_id.strip()
    if not normalized or len(normalized) > 160:
        raise HTTPException(status_code=400, detail={"error_code": "invalid_request_id"})

    sections = {
        "provider_runtime": _provider_runtime_audit_rows(db, normalized),
        "admin_audit": _admin_audit_rows(db, normalized),
        "tool_calls": _tool_call_rows(db, normalized),
        "ticket_timeline": _ticket_timeline_rows(db, normalized),
        "outbound_messages": _outbound_rows(db, normalized),
        "integration_requests": _integration_rows(db, normalized),
    }
    summary = _request_trace_summary(sections)
    return {
        "request_id": normalized,
        "found": any(summary[key] > 0 for key in (
            "provider_runtime_count",
            "admin_audit_count",
            "tool_call_count",
            "timeline_count",
            "outbound_count",
            "integration_count",
        )),
        "summary": summary,
        "sections": sections,
    }


@router.patch("/routing/webchat-fast-reply")
def update_webchat_fast_reply_routing(
    payload: WebchatFastRoutingUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        payload.validate_allowed()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": str(exc)}) from exc

    existing = db.execute(text("""
        SELECT id
        FROM provider_routing_rules
        WHERE tenant_id = :tenant_id
          AND channel_key = :channel_key
          AND scenario = :scenario
        LIMIT 1
    """), {
        "tenant_id": payload.tenant_id,
        "channel_key": payload.channel_key,
        "scenario": _WEBCHAT_FAST_SCENARIO,
    }).mappings().first()

    params = {
        "id": existing["id"] if existing else str(uuid.uuid4()),
        "tenant_id": payload.tenant_id,
        "channel_key": payload.channel_key,
        "scenario": _WEBCHAT_FAST_SCENARIO,
        "primary_provider": payload.primary_provider,
        "fallback_providers": json.dumps(payload.fallback_providers, separators=(",", ":")),
        "output_contract": _WEBCHAT_FAST_OUTPUT_CONTRACT,
        "timeout_ms": payload.timeout_ms,
        "canary_percent": payload.canary_percent,
        "kill_switch": payload.kill_switch,
        "enabled": payload.enabled,
    }

    if existing:
        db.execute(text("""
            UPDATE provider_routing_rules
            SET primary_provider = :primary_provider,
                fallback_providers = :fallback_providers,
                output_contract = :output_contract,
                timeout_ms = :timeout_ms,
                canary_percent = :canary_percent,
                kill_switch = :kill_switch,
                enabled = :enabled,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), params)
    else:
        db.execute(text("""
            INSERT INTO provider_routing_rules (
                id, tenant_id, channel_key, scenario, primary_provider, fallback_providers,
                output_contract, timeout_ms, canary_percent, kill_switch, enabled, created_at, updated_at
            )
            VALUES (
                :id, :tenant_id, :channel_key, :scenario, :primary_provider, :fallback_providers,
                :output_contract, :timeout_ms, :canary_percent, :kill_switch, :enabled,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """), params)
    db.commit()
    return {
        "ok": True,
        "routing_rule": {
            **params,
            "fallback_providers": payload.fallback_providers,
        },
    }
