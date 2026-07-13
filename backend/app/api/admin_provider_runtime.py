from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_can_manage_runtime
from ..services.provider_runtime.traffic_selection import (
    ALLOWED_CANARY_PERCENTAGES,
    safe_traffic_configuration,
)
from ..services.provider_runtime_status import get_provider_runtime_status
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/provider-runtime", tags=["admin-provider-runtime"])

_ALLOWED_PRIMARY_PROVIDERS = {"private_ai_runtime"}
_WEBCHAT_RUNTIME_SCENARIO = "webchat_runtime_reply"
_WEBCHAT_RUNTIME_OUTPUT_CONTRACT = "nexus_webchat_runtime_reply_v1"


class WebchatRuntimeRoutingUpdate(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=36)
    channel_key: str = Field(default="website", min_length=1, max_length=100)
    primary_provider: str = Field(default="private_ai_runtime", min_length=1, max_length=100)
    fallback_providers: list[str] = Field(default_factory=list, max_length=3)
    canary_percent: int = Field(default=0, ge=0, le=100)
    kill_switch: bool = False
    enabled: bool = True
    timeout_ms: int = Field(default=10000, ge=1000, le=30000)

    @field_validator("canary_percent")
    @classmethod
    def validate_canary_stage(cls, value: int) -> int:
        if value not in ALLOWED_CANARY_PERCENTAGES:
            raise ValueError("provider_runtime_canary_percent_invalid")
        return value

    def validate_allowed(self) -> None:
        if self.primary_provider not in _ALLOWED_PRIMARY_PROVIDERS:
            raise ValueError("primary_provider_not_allowed")
        if self.fallback_providers:
            raise ValueError("fallback_provider_not_allowed")


def _traffic_routing_rules(db: Session) -> dict[str, Any]:
    try:
        statement = text(
            """
            SELECT tenant_id, channel_key, primary_provider, canary_percent,
                   kill_switch, enabled, updated_at
            FROM provider_routing_rules
            WHERE scenario = :scenario
            ORDER BY tenant_id ASC, channel_key ASC
            LIMIT 101
            """
        ).columns(kill_switch=Boolean(), enabled=Boolean())
        rows = db.execute(statement, {"scenario": _WEBCHAT_RUNTIME_SCENARIO}).mappings().all()
    except Exception:
        return {
            "status": "unavailable",
            "reason_code": "provider_runtime_routing_rules_unavailable",
            "items": [],
            "truncated": False,
        }

    truncated = len(rows) > 100
    items: list[dict[str, Any]] = []
    invalid_found = False
    for row in rows[:100]:
        traffic = safe_traffic_configuration(
            default_canary_percent=row["canary_percent"],
            default_kill_switch=row["kill_switch"],
            default_mode="canary",
        )
        invalid_found = invalid_found or bool(traffic["configuration_errors"])
        items.append(
            {
                "tenant_id": str(row["tenant_id"] or "")[:120],
                "channel_key": str(row["channel_key"] or "")[:120],
                "primary_provider": str(row["primary_provider"] or "")[:100],
                "enabled": bool(row["enabled"]),
                "traffic_selection": traffic,
                "updated_at": row["updated_at"].isoformat()
                if hasattr(row["updated_at"], "isoformat")
                else row["updated_at"],
            }
        )
    return {
        "status": "misconfigured" if invalid_found else "ready",
        "reason_code": "provider_runtime_routing_rule_invalid" if invalid_found else None,
        "items": items,
        "truncated": truncated,
    }


@router.get("/status")
def provider_runtime_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    snapshot = get_provider_runtime_status(db)
    traffic = safe_traffic_configuration(
        default_canary_percent=0,
        default_kill_switch=False,
        default_mode="control",
    )
    traffic["scope"] = "global_defaults_and_environment_overrides"
    traffic["webchat_runtime_rules"] = _traffic_routing_rules(db)
    snapshot["traffic_selection"] = traffic

    configuration_errors = list(traffic.get("configuration_errors") or [])
    routing_status = traffic["webchat_runtime_rules"]["status"]
    if configuration_errors or routing_status != "ready":
        warnings = list(snapshot.get("warnings") or [])
        warnings.extend(f"provider_runtime traffic configuration invalid: {code}" for code in configuration_errors)
        if routing_status == "misconfigured":
            warnings.append("provider_runtime routing rules are misconfigured")
        elif routing_status == "unavailable":
            warnings.append("provider_runtime routing rules are unavailable")
        snapshot["warnings"] = warnings
        snapshot["ok"] = False
        snapshot["status"] = "misconfigured"
    return snapshot


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
    rows = db.execute(
        text(
            f"""
            SELECT id, tenant_id, provider, request_id, channel_key, session_id,
                   operation, status, safe_summary, error_code, elapsed_ms, created_at
            FROM provider_runtime_audit_logs
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    items = []
    for row in rows:
        safe_summary = row["safe_summary"]
        if isinstance(safe_summary, str):
            try:
                safe_summary = json.loads(safe_summary)
            except json.JSONDecodeError:
                safe_summary = {}
        items.append(
            {
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
                "created_at": row["created_at"].isoformat()
                if hasattr(row["created_at"], "isoformat")
                else row["created_at"],
            }
        )
    return {"items": items, "total": len(items)}


@router.patch("/routing/webchat-runtime")
def update_webchat_runtime_routing(
    payload: WebchatRuntimeRoutingUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        payload.validate_allowed()
    except ValueError:
        error_code = (
            "primary_provider_not_allowed"
            if payload.primary_provider not in _ALLOWED_PRIMARY_PROVIDERS
            else "fallback_provider_not_allowed"
        )
        raise HTTPException(status_code=400, detail={"error_code": error_code}) from None

    existing = db.execute(
        text(
            """
            SELECT id
            FROM provider_routing_rules
            WHERE tenant_id = :tenant_id
              AND channel_key = :channel_key
              AND scenario = :scenario
            LIMIT 1
            """
        ),
        {
            "tenant_id": payload.tenant_id,
            "channel_key": payload.channel_key,
            "scenario": _WEBCHAT_RUNTIME_SCENARIO,
        },
    ).mappings().first()

    params = {
        "id": existing["id"] if existing else str(uuid.uuid4()),
        "tenant_id": payload.tenant_id,
        "channel_key": payload.channel_key,
        "scenario": _WEBCHAT_RUNTIME_SCENARIO,
        "primary_provider": payload.primary_provider,
        "fallback_providers": json.dumps(payload.fallback_providers, separators=(",", ":")),
        "output_contract": _WEBCHAT_RUNTIME_OUTPUT_CONTRACT,
        "timeout_ms": payload.timeout_ms,
        "canary_percent": payload.canary_percent,
        "kill_switch": payload.kill_switch,
        "enabled": payload.enabled,
    }

    if existing:
        db.execute(
            text(
                """
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
                """
            ),
            params,
        )
    else:
        db.execute(
            text(
                """
                INSERT INTO provider_routing_rules (
                    id, tenant_id, channel_key, scenario, primary_provider, fallback_providers,
                    output_contract, timeout_ms, canary_percent, kill_switch, enabled, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :channel_key, :scenario, :primary_provider, :fallback_providers,
                    :output_contract, :timeout_ms, :canary_percent, :kill_switch, :enabled,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            params,
        )
    db.commit()
    return {
        "ok": True,
        "routing_rule": {
            **params,
            "fallback_providers": payload.fallback_providers,
            "traffic_selection": safe_traffic_configuration(
                default_canary_percent=payload.canary_percent,
                default_kill_switch=payload.kill_switch,
                default_mode="canary",
            ),
        },
    }
