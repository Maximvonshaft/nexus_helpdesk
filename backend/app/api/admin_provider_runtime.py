from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy import Boolean, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_can_manage_runtime
from ..services.provider_runtime.router import persisted_provider_alias_errors
from ..services.provider_runtime.traffic_selection import (
    ALLOWED_CANARY_PERCENTS,
    safe_traffic_configuration,
)
from ..services.provider_runtime_status import get_provider_runtime_status
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/provider-runtime", tags=["admin-provider-runtime"])

_ALLOWED_PRIMARY_PROVIDERS = {"private_ai_runtime"}
_WEBCHAT_RUNTIME_SCENARIO = "webchat_runtime_reply"
_WEBCHAT_RUNTIME_OUTPUT_CONTRACT = "nexus_webchat_runtime_reply_v1"
_CANARY_PERCENT_INVALID = "provider_runtime_canary_percent_invalid"
_KILL_SWITCH_INVALID = "provider_runtime_kill_switch_invalid"
_PROVIDER_ALIAS_INVALID = "provider_runtime_provider_alias_invalid"
_PROVIDER_SETTINGS_INVALID = "provider_runtime_settings_invalid"
_HUMAN_WEBCALL_RECORDING_WARNING = "human_webcall recording is enabled"
_HUMAN_WEBCALL_TRANSCRIPTION_WARNING = "human_webcall transcription is enabled"
_HUMAN_WEBCALL_STATUS_UNAVAILABLE = "human_webcall status unavailable"
_HUMAN_WEBCALL_CONFIG_INVALID = "human_webcall runtime configuration invalid"
_ALLOWED_FALLBACK_RESULTS = {
    "blocked",
    "not_configured",
    "not_attempted",
    "pending",
    "succeeded",
    "failed",
}
_ALLOWED_AUDIT_OPERATIONS = {
    "traffic_select",
    "generate",
    "shadow_generate",
    "parse_reject",
    "shadow_parse_reject",
}
_ALLOWED_AUDIT_STATUSES = {"blocked", "failed", "ok", "skipped"}
_ALLOWED_TRAFFIC_MODES = {"invalid", "control", "canary", "shadow"}
_ALLOWED_TRAFFIC_PATHS = {
    "control",
    "canary_authoritative",
    "shadow_only",
    "kill_switch",
}
_ALLOWED_TRAFFIC_REASONS = {
    "provider_runtime_traffic_mode_invalid",
    "provider_runtime_canary_percent_invalid",
    "provider_runtime_kill_switch_invalid",
    "provider_runtime_primary_provider_invalid",
    "provider_runtime_provider_alias_invalid",
    "provider_runtime_provider_not_allowed",
    "provider_runtime_traffic_configuration_invalid",
    "control_mode_configured",
    "shadow_mode_configured",
    "canary_percent_zero",
    "bucket_selected",
    "bucket_not_selected",
    "kill_switch_active",
}
_ALLOWED_AUDIT_ERROR_CODES = {
    "adapter_not_registered",
    "all_providers_failed",
    "kill_switch_active",
    "parse_reject",
    "private_ai_runtime_failed",
    "provider_canary_control_path",
    "provider_runtime_canary_percent_invalid",
    "provider_runtime_kill_switch_invalid",
    "provider_runtime_primary_provider_invalid",
    "provider_runtime_provider_alias_invalid",
    "provider_runtime_provider_failed",
    "provider_runtime_provider_not_allowed",
    "provider_runtime_traffic_configuration_invalid",
    "provider_runtime_traffic_mode_invalid",
    "provider_shadow_completed",
    "provider_shadow_failed",
    "provider_timeout",
}


def _sanitize_provider_runtime_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(snapshot or {})
    if "config_error" in sanitized:
        sanitized["config_error"] = _PROVIDER_SETTINGS_INVALID

    human = sanitized.get("human_webcall")
    if isinstance(human, dict):
        safe_human = dict(human)
        safe_warnings: list[str] = []
        for warning in human.get("warnings") or []:
            if warning == _HUMAN_WEBCALL_RECORDING_WARNING:
                safe_warnings.append(_HUMAN_WEBCALL_RECORDING_WARNING)
            elif warning == _HUMAN_WEBCALL_TRANSCRIPTION_WARNING:
                safe_warnings.append(_HUMAN_WEBCALL_TRANSCRIPTION_WARNING)
            elif isinstance(warning, str) and warning.startswith(
                _HUMAN_WEBCALL_STATUS_UNAVAILABLE
            ):
                safe_warnings.append(_HUMAN_WEBCALL_STATUS_UNAVAILABLE)
            else:
                safe_warnings.append(_HUMAN_WEBCALL_CONFIG_INVALID)
        safe_human["warnings"] = list(dict.fromkeys(safe_warnings))
        sanitized["human_webcall"] = safe_human
    return sanitized


class WebchatRuntimeRoutingUpdate(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=36)
    channel_key: str = Field(default="website", min_length=1, max_length=100)
    primary_provider: str = Field(
        default="private_ai_runtime",
        min_length=1,
        max_length=100,
    )
    fallback_providers: list[str] = Field(default_factory=list, max_length=3)
    canary_percent: StrictInt = Field(default=0, ge=0, le=100)
    kill_switch: bool = False
    enabled: bool = True
    timeout_ms: int = Field(default=10000, ge=1000, le=30000)

    def validate_allowed(self) -> None:
        if self.primary_provider not in _ALLOWED_PRIMARY_PROVIDERS:
            raise ValueError("primary_provider_not_allowed")
        if self.fallback_providers:
            raise ValueError("fallback_provider_not_allowed")
        if self.canary_percent not in ALLOWED_CANARY_PERCENTS:
            raise ValueError(_CANARY_PERCENT_INVALID)


def _database_configuration_errors(selection: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if selection.get("default_canary_percent") is None:
        errors.append(_CANARY_PERCENT_INVALID)
    if selection.get("default_kill_switch") is None:
        errors.append(_KILL_SWITCH_INVALID)
    return errors


def _traffic_routing_rules(db: Session) -> dict[str, Any]:
    try:
        statement = (
            text(
                """
                SELECT scenario, tenant_id, channel_key, primary_provider,
                       fallback_providers, canary_percent, kill_switch,
                       enabled, updated_at
                FROM provider_routing_rules
                ORDER BY tenant_id ASC, channel_key ASC, scenario ASC
                """
            )
            .columns(kill_switch=Boolean(), enabled=Boolean())
            .execution_options(stream_results=True, yield_per=500)
        )
        rows = db.execute(statement).mappings()

        output: list[dict[str, Any]] = []
        invalid_rule_found = False
        truncated = False
        for index, row in enumerate(rows):
            selection = safe_traffic_configuration(
                default_canary_percent=row["canary_percent"],
                default_kill_switch=row["kill_switch"],
            )
            database_errors = _database_configuration_errors(selection)
            alias_errors = persisted_provider_alias_errors(
                primary_provider=row["primary_provider"],
                fallback_providers=row["fallback_providers"],
            )
            if alias_errors:
                database_errors.append(_PROVIDER_ALIAS_INVALID)
            database_errors = list(dict.fromkeys(database_errors))
            invalid_rule_found = invalid_rule_found or bool(database_errors)

            if index >= 100:
                truncated = True
                continue

            output.append(
                {
                    "scenario": str(row["scenario"] or "")[:120],
                    "tenant_id": str(row["tenant_id"] or "")[:120],
                    "channel_key": str(row["channel_key"] or "")[:120],
                    "primary_provider": (
                        str(row["primary_provider"] or "")[:100]
                        if not alias_errors
                        else "invalid"
                    ),
                    "enabled": bool(row["enabled"]),
                    "database_canary_percent": selection.get(
                        "default_canary_percent"
                    ),
                    "database_kill_switch": selection.get(
                        "default_kill_switch"
                    ),
                    "database_configuration_errors": database_errors,
                    "effective_traffic_selection": selection,
                    "updated_at": (
                        row["updated_at"].isoformat()
                        if hasattr(row["updated_at"], "isoformat")
                        else row["updated_at"]
                    ),
                }
            )
    except Exception:
        return {
            "status": "unavailable",
            "reason_code": "provider_runtime_routing_rules_unavailable",
            "items": [],
            "truncated": False,
        }

    return {
        "status": "misconfigured" if invalid_rule_found else "ready",
        "reason_code": (
            "provider_runtime_routing_rule_invalid"
            if invalid_rule_found
            else None
        ),
        "items": output,
        "truncated": truncated,
    }


@router.get("/status")
def provider_runtime_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    snapshot = _sanitize_provider_runtime_snapshot(
        get_provider_runtime_status(db)
    )
    traffic = safe_traffic_configuration()
    traffic["scope"] = "global_defaults_and_environment_overrides"
    traffic["webchat_runtime_rules"] = _traffic_routing_rules(db)
    snapshot["traffic_selection"] = traffic

    configuration_errors = list(traffic.get("configuration_errors") or [])
    routing_rules_status = traffic["webchat_runtime_rules"].get("status")
    if configuration_errors or routing_rules_status != "ready":
        warnings = list(snapshot.get("warnings") or [])
        warnings.extend(
            f"provider_runtime traffic configuration invalid: {code}"
            for code in configuration_errors
        )
        if routing_rules_status == "misconfigured":
            warnings.append("provider_runtime routing rules are misconfigured")
        elif routing_rules_status == "unavailable":
            warnings.append("provider_runtime routing rules are unavailable")
        snapshot["warnings"] = warnings
        snapshot["ok"] = False
        snapshot["status"] = (
            "misconfigured"
            if configuration_errors or routing_rules_status == "misconfigured"
            else "unavailable"
        )
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
    params: dict[str, Any] = {"limit": capped_limit}
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
                "tenant_id": str(row["tenant_id"] or "")[:120],
                "provider": (
                    row["provider"]
                    if row["provider"] in {"router", "private_ai_runtime"}
                    else "invalid"
                ),
                "request_id": str(row["request_id"] or "")[:160],
                "channel_key": str(row["channel_key"] or "")[:120],
                "session_id": str(row["session_id"] or "")[:160],
                "operation": (
                    row["operation"]
                    if row["operation"] in _ALLOWED_AUDIT_OPERATIONS
                    else "invalid"
                ),
                "status": (
                    row["status"]
                    if row["status"] in _ALLOWED_AUDIT_STATUSES
                    else "invalid"
                ),
                "safe_summary": _sanitize_audit_summary(safe_summary),
                "error_code": _sanitize_error_code(row["error_code"]),
                "elapsed_ms": max(
                    0,
                    min(int(row["elapsed_ms"] or 0), 120000),
                ),
                "created_at": (
                    row["created_at"].isoformat()
                    if hasattr(row["created_at"], "isoformat")
                    else row["created_at"]
                ),
            }
        )
    return {"items": items, "total": len(items)}


def _sanitize_audit_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}

    fallback_result = value.get("fallback_result")
    if fallback_result in _ALLOWED_FALLBACK_RESULTS:
        output["fallback_result"] = fallback_result

    if isinstance(value.get("unavailable"), bool):
        output["unavailable"] = value["unavailable"]
    if value.get("provider_result") in {"failed", "succeeded"}:
        output["provider_result"] = value["provider_result"]
    if value.get("parse_error_code") == "output_contract_rejected":
        output["parse_error_code"] = "output_contract_rejected"
    if value.get("shadow_result") in {"succeeded", "failed"}:
        output["shadow_result"] = value["shadow_result"]

    traffic = value.get("traffic_selection")
    if isinstance(traffic, dict):
        safe_traffic: dict[str, Any] = {}
        if (
            traffic.get("schema_version")
            == "nexus.provider_runtime.traffic_selection.v1"
        ):
            safe_traffic["schema_version"] = traffic["schema_version"]
        if traffic.get("configured_mode") in _ALLOWED_TRAFFIC_MODES:
            safe_traffic["configured_mode"] = traffic["configured_mode"]
        if traffic.get("path") in _ALLOWED_TRAFFIC_PATHS:
            safe_traffic["path"] = traffic["path"]
        if traffic.get("reason") in _ALLOWED_TRAFFIC_REASONS:
            safe_traffic["reason"] = traffic["reason"]
        for key in ("execute_candidate", "authoritative"):
            if isinstance(traffic.get(key), bool):
                safe_traffic[key] = traffic[key]
        canary_percent = traffic.get("canary_percent")
        if canary_percent is None or (
            isinstance(canary_percent, int)
            and not isinstance(canary_percent, bool)
            and canary_percent in ALLOWED_CANARY_PERCENTS
        ):
            safe_traffic["canary_percent"] = canary_percent
        bucket = traffic.get("bucket")
        if bucket is None or (
            isinstance(bucket, int)
            and not isinstance(bucket, bool)
            and 0 <= bucket <= 99
        ):
            safe_traffic["bucket"] = bucket
        errors = traffic.get("configuration_errors")
        if isinstance(errors, list):
            safe_traffic["configuration_errors"] = [
                item
                for item in errors
                if item in _ALLOWED_TRAFFIC_REASONS
            ][:8]
        if safe_traffic:
            output["traffic_selection"] = safe_traffic
    return output


def _sanitize_error_code(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return "provider_runtime_error_invalid"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_AUDIT_ERROR_CODES:
        return normalized
    return "provider_runtime_error_invalid"


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
        if payload.primary_provider not in _ALLOWED_PRIMARY_PROVIDERS:
            error_code = "primary_provider_not_allowed"
        elif payload.fallback_providers:
            error_code = "fallback_provider_not_allowed"
        else:
            error_code = _CANARY_PERCENT_INVALID
        raise HTTPException(
            status_code=400,
            detail={"error_code": error_code},
        ) from None

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
        "fallback_providers": json.dumps(
            payload.fallback_providers,
            separators=(",", ":"),
        ),
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
                    id, tenant_id, channel_key, scenario,
                    primary_provider, fallback_providers,
                    output_contract, timeout_ms, canary_percent,
                    kill_switch, enabled, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :channel_key, :scenario,
                    :primary_provider, :fallback_providers,
                    :output_contract, :timeout_ms, :canary_percent,
                    :kill_switch, :enabled,
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
            ),
        },
    }
