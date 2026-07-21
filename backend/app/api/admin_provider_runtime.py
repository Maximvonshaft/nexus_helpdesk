from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.provider_runtime.output_contracts import (
    AGENT_SPECIALIST_OUTPUT_CONTRACT,
    AGENT_TURN_OUTPUT_CONTRACT,
)
from ..services.provider_runtime_status import get_provider_runtime_status
from ..services.runtime_permissions import (
    ensure_can_manage_runtime,
    ensure_can_read_runtime,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/provider-runtime", tags=["admin-provider-runtime"])

_ALLOWED_PRIMARY_PROVIDERS = {"private_ai_runtime"}
_ROUTING_CONTRACTS = {
    "agent_turn": AGENT_TURN_OUTPUT_CONTRACT,
    "agent_specialist": AGENT_SPECIALIST_OUTPUT_CONTRACT,
}


class AgentTurnRoutingUpdate(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=36)
    channel_key: str = Field(default="website", min_length=1, max_length=100)
    primary_provider: str = Field(
        default="private_ai_runtime", min_length=1, max_length=100
    )
    fallback_providers: list[str] = Field(default_factory=list, max_length=3)
    canary_percent: int = Field(default=0, ge=0, le=100)
    kill_switch: bool = False
    enabled: bool = True
    timeout_ms: int = Field(default=10000, ge=1000, le=30000)

    def validate_allowed(self) -> None:
        if self.primary_provider not in _ALLOWED_PRIMARY_PROVIDERS:
            raise ValueError("primary_provider_not_allowed")
        if self.fallback_providers:
            raise ValueError("fallback_provider_not_allowed")


@router.get("/status")
def provider_runtime_status(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    ensure_can_read_runtime(current_user, db)
    return get_provider_runtime_status(db)


@router.get("/audit/recent")
def provider_runtime_audit_recent(
    request_id: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_runtime(current_user, db)
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
                "safe_summary": (
                    safe_summary if isinstance(safe_summary, dict) else {}
                ),
                "error_code": row["error_code"],
                "elapsed_ms": row["elapsed_ms"],
                "created_at": (
                    row["created_at"].isoformat()
                    if hasattr(row["created_at"], "isoformat")
                    else row["created_at"]
                ),
            }
        )
    return {"items": items, "total": len(items)}


@router.patch("/routing/agent-turn")
def update_agent_turn_routing(
    payload: AgentTurnRoutingUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Atomically align parent-Agent and Specialist Provider safety controls."""

    ensure_can_manage_runtime(current_user, db)
    try:
        payload.validate_allowed()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": str(exc)},
        ) from exc

    rules = [
        _upsert_routing_rule(
            db,
            payload=payload,
            scenario=scenario,
            output_contract=output_contract,
        )
        for scenario, output_contract in _ROUTING_CONTRACTS.items()
    ]
    db.commit()
    parent_rule = next(item for item in rules if item["scenario"] == "agent_turn")
    return {
        "ok": True,
        # Preserve the existing response authority for current clients while the
        # complete aligned set is exposed explicitly.
        "routing_rule": parent_rule,
        "routing_rules": rules,
    }


def _upsert_routing_rule(
    db: Session,
    *,
    payload: AgentTurnRoutingUpdate,
    scenario: str,
    output_contract: str,
) -> dict:
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
            "scenario": scenario,
        },
    ).mappings().first()
    params = {
        "id": existing["id"] if existing else str(uuid.uuid4()),
        "tenant_id": payload.tenant_id,
        "channel_key": payload.channel_key,
        "scenario": scenario,
        "primary_provider": payload.primary_provider,
        "fallback_providers": json.dumps(
            payload.fallback_providers,
            separators=(",", ":"),
        ),
        "output_contract": output_contract,
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
                    id, tenant_id, channel_key, scenario, primary_provider,
                    fallback_providers, output_contract, timeout_ms,
                    canary_percent, kill_switch, enabled, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :channel_key, :scenario, :primary_provider,
                    :fallback_providers, :output_contract, :timeout_ms,
                    :canary_percent, :kill_switch, :enabled,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            params,
        )
    return {
        **params,
        "fallback_providers": payload.fallback_providers,
    }
