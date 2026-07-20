from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .health import ProviderRuntimeHealth
from .output_contracts import OutputContracts, WEBCHAT_RUNTIME_OUTPUT_CONTRACT
from .registry import ProviderRegistry
from .schemas import ProviderRequest, ProviderResult
from .traffic_selection import (
    ProviderTrafficPath,
    effective_canary_percent,
    effective_kill_switch,
    select_provider_traffic,
)

logger = logging.getLogger(__name__)
_APPROVED_PROVIDERS = frozenset({"private_ai_runtime"})
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./:+-]{1,160}$")


class ProviderRuntimeRouter:
    """Provider selection, health and audit. Business semantics do not belong here."""

    def __init__(self, db: Session):
        self.db = db

    async def route(self, request: ProviderRequest) -> ProviderResult:
        from . import bootstrap_provider_runtime

        bootstrap_provider_runtime()
        raw = _load_rule(self.db, request)
        try:
            config = _effective_configuration(raw)
            kill_switch = effective_kill_switch(raw["kill_switch"])
            traffic = select_provider_traffic(
                request,
                canary_percent=config["canary_percent"],
                kill_switch=kill_switch,
            )
        except Exception as exc:
            return self._reject(request, "provider_runtime_configuration_invalid", type(exc).__name__)

        if kill_switch:
            return self._unavailable_with_audit(request, "kill_switch_active", traffic=traffic)
        if traffic.path == ProviderTrafficPath.CONTROL:
            return self._unavailable_with_audit(request, "provider_canary_control_path", traffic=traffic)

        output_contract = config["output_contract"]
        if not OutputContracts.get_schema(output_contract):
            return self._unavailable_with_audit(
                request,
                "provider_runtime_output_contract_invalid",
                traffic=traffic,
            )
        request.output_contract = output_contract
        request.timeout_ms = config["timeout_ms"]
        providers = list(dict.fromkeys([config["primary_provider"], *config["fallbacks"]]))
        shadow_only = traffic.path == ProviderTrafficPath.SHADOW_ONLY
        operation = "shadow_generate" if shadow_only else "generate"
        last_elapsed_ms = 0

        for provider_name in providers:
            health = ProviderRuntimeHealth.should_skip(provider_name)
            if health.skip:
                self._write_audit(
                    request,
                    operation=operation,
                    status="skipped",
                    provider=provider_name,
                    elapsed_ms=0,
                    safe_summary={"traffic": _traffic_summary(traffic), "provider_health": health.safe_summary()},
                    error_code=health.reason or "provider_health_skip",
                )
                continue
            adapter = ProviderRegistry.get(provider_name, self.db)
            if adapter is None:
                self._write_audit(
                    request,
                    operation=operation,
                    status="failed",
                    provider=provider_name,
                    elapsed_ms=0,
                    safe_summary={"traffic": _traffic_summary(traffic)},
                    error_code="adapter_not_registered",
                )
                continue

            result = await adapter.generate(self.db, request)
            last_elapsed_ms = max(last_elapsed_ms, int(result.elapsed_ms or 0))
            if not result.ok:
                error_code = _bounded_error_code(result.error_code)
                health_event = ProviderRuntimeHealth.record_failure(provider_name, error_code)
                summary = {
                    "traffic": _traffic_summary(traffic),
                    "provider": _bounded_summary(result.raw_payload_safe_summary),
                    "provider_error_category": error_code,
                }
                if health_event:
                    summary["provider_health"] = health_event
                result.error_code = error_code
                result.raw_payload_safe_summary = summary
                self._write_audit(
                    request,
                    operation=operation,
                    status="failed",
                    provider=provider_name,
                    elapsed_ms=result.elapsed_ms,
                    safe_summary=summary,
                    error_code=error_code,
                )
                if not result.fallback_allowed:
                    return result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("provider_output_missing")
                parsed = OutputContracts.validate_and_parse(
                    output_contract,
                    json.dumps(result.structured_output, ensure_ascii=False),
                )
            except Exception as exc:
                ProviderRuntimeHealth.record_failure(provider_name, "provider_output_invalid")
                self._write_audit(
                    request,
                    operation="shadow_parse_reject" if shadow_only else "parse_reject",
                    status="failed",
                    provider=provider_name,
                    elapsed_ms=result.elapsed_ms,
                    safe_summary={
                        "traffic": _traffic_summary(traffic),
                        "parse_error": type(exc).__name__,
                    },
                    error_code="provider_output_invalid",
                )
                continue

            result.structured_output = parsed
            health_event = ProviderRuntimeHealth.record_success(provider_name)
            summary = {
                "traffic": _traffic_summary(traffic),
                "provider": _bounded_summary(result.raw_payload_safe_summary),
            }
            if health_event:
                summary["provider_health"] = health_event
            result.raw_payload_safe_summary = summary
            self._write_audit(
                request,
                operation=operation,
                status="shadow_ok" if shadow_only else "ok",
                provider=provider_name,
                elapsed_ms=result.elapsed_ms,
                safe_summary=summary,
            )
            if shadow_only:
                return ProviderResult.unavailable(
                    provider="provider_runtime",
                    error_code="provider_shadow_only",
                    elapsed_ms=result.elapsed_ms,
                    fallback_allowed=False,
                )
            return result

        self._write_audit(
            request,
            operation=operation,
            status="failed",
            provider="router",
            elapsed_ms=last_elapsed_ms,
            safe_summary={"traffic": _traffic_summary(traffic)},
            error_code="all_providers_failed",
        )
        return ProviderResult.unavailable(
            provider="router",
            error_code="all_providers_failed",
            elapsed_ms=last_elapsed_ms,
            fallback_allowed=False,
        )

    def _unavailable_with_audit(self, request: ProviderRequest, code: str, *, traffic: Any) -> ProviderResult:
        summary = {"traffic": _traffic_summary(traffic)}
        self._write_audit(
            request,
            operation="traffic_select",
            status="skipped",
            provider="router",
            elapsed_ms=0,
            safe_summary=summary,
            error_code=code,
        )
        result = ProviderResult.unavailable("router", code, 0, fallback_allowed=False)
        result.raw_payload_safe_summary = summary
        return result

    def _reject(self, request: ProviderRequest, code: str, reason: str) -> ProviderResult:
        summary = {"configuration_error": reason[:120]}
        self._write_audit(
            request,
            operation="traffic_select",
            status="failed",
            provider="router",
            elapsed_ms=0,
            safe_summary=summary,
            error_code=code,
        )
        result = ProviderResult.unavailable("router", code, 0, fallback_allowed=False)
        result.raw_payload_safe_summary = summary
        return result

    def _write_audit(
        self,
        request: ProviderRequest,
        *,
        operation: str,
        status: str,
        provider: str,
        elapsed_ms: int,
        safe_summary: dict[str, Any] | None,
        error_code: str | None = None,
    ) -> None:
        try:
            self.db.execute(
                text(
                    """
                    INSERT INTO provider_runtime_audit_logs
                    (id, tenant_id, provider, request_id, channel_key, session_id,
                     operation, status, safe_summary, error_code, elapsed_ms, created_at)
                    VALUES (:id, :tenant_id, :provider, :request_id, :channel_key,
                            :session_id, :operation, :status, :safe_summary,
                            :error_code, :elapsed_ms, :now)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": request.tenant_id,
                    "provider": provider,
                    "request_id": request.request_id,
                    "channel_key": request.channel_key,
                    "session_id": request.session_id,
                    "operation": operation,
                    "status": status,
                    "safe_summary": json.dumps(safe_summary or {}, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                    "error_code": error_code,
                    "elapsed_ms": elapsed_ms,
                    "now": datetime.now(timezone.utc),
                },
            )
            self.db.commit()
        except Exception:
            logger.error("provider_runtime_audit_write_failed")
            self.db.rollback()


def _load_rule(db: Session, request: ProviderRequest) -> dict[str, Any]:
    rule = db.execute(
        text(
            """
            SELECT primary_provider, fallback_providers, output_contract,
                   timeout_ms, kill_switch, canary_percent
            FROM provider_routing_rules
            WHERE tenant_id = :tenant_id
              AND channel_key = :channel
              AND scenario = :scenario
              AND enabled = true
            """
        ),
        {"tenant_id": request.tenant_id, "channel": request.channel_key, "scenario": request.scenario},
    ).mappings().first()
    if rule is None:
        return {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": WEBCHAT_RUNTIME_OUTPUT_CONTRACT,
            "timeout_ms": 15000,
            "kill_switch": False,
            "canary_percent": 0,
        }
    return dict(rule)


def _effective_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    primary = (os.getenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER") or str(raw.get("primary_provider") or "")).strip()
    if primary not in _APPROVED_PROVIDERS:
        raise ValueError("primary_provider_invalid")
    raw_fallbacks = os.getenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS")
    fallbacks = _coerce_fallbacks(raw.get("fallback_providers") if raw_fallbacks is None else raw_fallbacks)
    if any(name not in _APPROVED_PROVIDERS for name in fallbacks):
        raise ValueError("fallback_provider_invalid")
    contract = (os.getenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT") or str(raw.get("output_contract") or "")).strip()
    if not contract:
        contract = WEBCHAT_RUNTIME_OUTPUT_CONTRACT
    timeout_raw = os.getenv("PROVIDER_RUNTIME_TIMEOUT_MS") or raw.get("timeout_ms") or 15000
    timeout_ms = int(timeout_raw)
    if not 500 <= timeout_ms <= 120000:
        raise ValueError("timeout_invalid")
    return {
        "primary_provider": primary,
        "fallbacks": fallbacks,
        "output_contract": contract,
        "timeout_ms": timeout_ms,
        "canary_percent": effective_canary_percent(raw.get("canary_percent")),
    }


def _coerce_fallbacks(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item).strip()]
    raise ValueError("fallback_provider_invalid")


def _traffic_summary(traffic: Any) -> dict[str, Any]:
    summary = traffic.safe_summary() if hasattr(traffic, "safe_summary") else {}
    return _bounded_summary(summary)


def _bounded_summary(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:160] if _SAFE_TOKEN.fullmatch(value[:160]) else "[REDACTED]"
    if isinstance(value, list):
        return [_bounded_summary(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded_summary(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    return type(value).__name__


def _bounded_error_code(value: str | None) -> str:
    candidate = str(value or "provider_call_failed").strip().lower().replace("-", "_")[:120]
    return candidate if _SAFE_TOKEN.fullmatch(candidate) else "provider_call_failed"
