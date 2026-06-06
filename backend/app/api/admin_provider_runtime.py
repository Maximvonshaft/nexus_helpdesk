from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_can_manage_runtime
from ..services.provider_runtime_status import get_provider_runtime_status
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/provider-runtime", tags=["admin-provider-runtime"])

_ALLOWED_PRIMARY_PROVIDERS = {"codex_app_server", "codex_direct", "openclaw_responses", "openai_responses"}
_ALLOWED_FALLBACK_PROVIDERS = {"openclaw_responses", "rule_engine", "openai_responses"}
_WEBCHAT_FAST_SCENARIO = "webchat_fast_reply"
_WEBCHAT_FAST_OUTPUT_CONTRACT = "speedaf_webchat_fast_reply_v1"


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
        if self.primary_provider == "codex_direct" and self.fallback_providers == ["openclaw_responses", "rule_engine"]:
            self.fallback_providers = ["rule_engine"]
        forbidden = [provider for provider in self.fallback_providers if provider not in _ALLOWED_FALLBACK_PROVIDERS]
        if forbidden:
            raise ValueError("fallback_provider_not_allowed")
        if self.primary_provider == "codex_app_server" and "openclaw_responses" not in self.fallback_providers:
            raise ValueError("codex_requires_openclaw_fallback")


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
