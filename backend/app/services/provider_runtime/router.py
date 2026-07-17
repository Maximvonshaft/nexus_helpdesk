from __future__ import annotations

import json
import logging
import os
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
    ProviderTrafficSelection,
    effective_canary_percent,
    effective_kill_switch,
    select_provider_traffic,
)

logger = logging.getLogger(__name__)

_APPROVED_PROVIDERS = frozenset({"private_ai_runtime"})
_CONFIGURATION_ERROR_CODES = frozenset(
    {
        "provider_runtime_traffic_mode_invalid",
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_kill_switch_invalid",
        "provider_runtime_primary_provider_invalid",
        "provider_runtime_fallback_provider_invalid",
        "provider_runtime_output_contract_invalid",
        "provider_runtime_timeout_ms_invalid",
    }
)


class ProviderRuntimeRouter:
    def __init__(self, db: Session):
        self.db = db

    def _write_audit(
        self,
        request: ProviderRequest,
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
                    (id, tenant_id, provider, request_id, channel_key, session_id, operation, status, safe_summary, error_code, elapsed_ms, created_at)
                    VALUES (:id, :tenant_id, :provider, :request_id, :channel_key, :session_id, :operation, :status, :safe_summary, :error_code, :elapsed_ms, :now)
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

    async def route(self, request: ProviderRequest) -> ProviderResult:
        from . import bootstrap_provider_runtime

        bootstrap_provider_runtime()
        raw = _load_rule(self.db, request)

        try:
            kill_switch = effective_kill_switch(raw["kill_switch"])
        except (TypeError, ValueError) as exc:
            return self._reject_configuration(request, _configuration_error_code(exc))

        if kill_switch:
            traffic = select_provider_traffic(
                request,
                canary_percent=raw["canary_percent"],
                kill_switch=True,
            )
            summary = _traffic_summary(traffic)
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                str(raw["primary_provider"] or "router"),
                0,
                summary,
                "kill_switch_active",
            )
            return _unavailable("kill_switch_active", summary)

        try:
            config = _effective_configuration(raw)
            traffic = select_provider_traffic(
                request,
                canary_percent=config["canary_percent"],
                kill_switch=False,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return self._reject_configuration(request, _configuration_error_code(exc))

        if traffic.path == ProviderTrafficPath.CONTROL:
            summary = _traffic_summary(traffic)
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                config["primary_provider"],
                0,
                summary,
                "provider_canary_control_path",
            )
            return _unavailable("provider_canary_control_path", summary)

        output_contract = config["output_contract"]
        if not OutputContracts.get_schema(output_contract):
            summary = _traffic_summary(traffic, {"configuration_error": "provider_runtime_output_contract_invalid"})
            self._write_audit(
                request,
                "traffic_select",
                "failed",
                "router",
                0,
                summary,
                "provider_runtime_output_contract_invalid",
            )
            return _unavailable("provider_runtime_output_contract_invalid", summary)

        request.output_contract = output_contract
        request.timeout_ms = config["timeout_ms"]
        providers = _dedupe_providers([config["primary_provider"], *config["fallbacks"]])
        shadow_only = traffic.path == ProviderTrafficPath.SHADOW_ONLY
        operation = "shadow_generate" if shadow_only else "generate"
        last_elapsed_ms = 0

        for provider_name in providers:
            health_decision = ProviderRuntimeHealth.should_skip(provider_name)
            if health_decision.skip:
                self._write_audit(
                    request,
                    operation,
                    "skipped",
                    provider_name,
                    0,
                    _traffic_summary(traffic, {"provider_health": health_decision.safe_summary()}),
                    health_decision.reason or "provider_health_skip",
                )
                continue

            adapter = ProviderRegistry.get(provider_name, self.db)
            if adapter is None:
                self._write_audit(
                    request,
                    operation,
                    "failed",
                    provider_name,
                    0,
                    _traffic_summary(traffic),
                    "adapter_not_registered",
                )
                continue

            result = await adapter.generate(self.db, request)
            last_elapsed_ms = max(last_elapsed_ms, int(result.elapsed_ms or 0))
            if not result.ok:
                safe_summary = _traffic_summary(traffic, dict(result.raw_payload_safe_summary or {}))
                health_event = ProviderRuntimeHealth.record_failure(provider_name, result.error_code)
                if health_event:
                    safe_summary["provider_health"] = health_event
                result.raw_payload_safe_summary = safe_summary
                self._write_audit(
                    request,
                    operation,
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    safe_summary,
                    result.error_code,
                )
                if not result.fallback_allowed:
                    return _shadow_result(traffic, result.elapsed_ms, safe_summary) if shadow_only else result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("provider_output_missing")
                parsed = OutputContracts.validate_and_parse(
                    output_contract,
                    json.dumps(result.structured_output),
                    request.tracking_fact_evidence_present,
                    (request.metadata or {}).get("persona_context"),
                    request.body,
                    (request.metadata or {}).get("knowledge_context"),
                )
            except Exception:
                safe_summary = _traffic_summary(traffic, {"parse_error": "provider_output_rejected"})
                health_event = ProviderRuntimeHealth.record_failure(provider_name, "parse_reject")
                if health_event:
                    safe_summary["provider_health"] = health_event
                self._write_audit(
                    request,
                    "shadow_parse_reject" if shadow_only else "parse_reject",
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    safe_summary,
                    "parse_reject",
                )
                continue

            result.structured_output = parsed
            safe_summary = _traffic_summary(traffic, dict(result.raw_payload_safe_summary or {}))
            health_event = ProviderRuntimeHealth.record_success(provider_name)
            if health_event:
                safe_summary["provider_health"] = health_event
            result.raw_payload_safe_summary = safe_summary
            self._write_audit(
                request,
                operation,
                "shadow_ok" if shadow_only else "ok",
                provider_name,
                result.elapsed_ms,
                safe_summary,
            )
            if shadow_only:
                return _shadow_result(traffic, result.elapsed_ms, safe_summary)
            return result

        summary = _traffic_summary(traffic)
        self._write_audit(
            request,
            operation,
            "failed",
            "router",
            last_elapsed_ms,
            summary,
            "all_providers_failed",
        )
        if shadow_only:
            return _shadow_result(traffic, last_elapsed_ms, summary)
        return _unavailable("all_providers_failed", summary, elapsed_ms=last_elapsed_ms)

    def _reject_configuration(self, request: ProviderRequest, reason: str) -> ProviderResult:
        summary = {
            "traffic": {
                "path": "blocked",
                "authoritative": False,
                "execute_candidate": False,
                "reason": reason,
            }
        }
        self._write_audit(request, "traffic_select", "failed", "router", 0, summary, reason)
        return _unavailable("provider_runtime_configuration_invalid", summary)


def _load_rule(db: Session, request: ProviderRequest) -> dict[str, Any]:
    rule = db.execute(
        text(
            """
            SELECT primary_provider, fallback_providers, output_contract, timeout_ms, kill_switch, canary_percent
            FROM provider_routing_rules
            WHERE tenant_id = :tenant_id
              AND channel_key = :channel
              AND scenario = :scenario
              AND enabled = true
            """
        ),
        {
            "tenant_id": request.tenant_id,
            "channel": request.channel_key,
            "scenario": request.scenario,
        },
    ).mappings().first()
    if rule is None:
        return {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": WEBCHAT_RUNTIME_OUTPUT_CONTRACT,
            "timeout_ms": 10000,
            "kill_switch": False,
            "canary_percent": 0,
        }
    return {
        "primary_provider": rule["primary_provider"],
        "fallback_providers": rule["fallback_providers"],
        "output_contract": rule["output_contract"],
        "timeout_ms": rule["timeout_ms"],
        "kill_switch": rule["kill_switch"],
        "canary_percent": rule["canary_percent"],
    }


def _effective_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    primary_provider = os.getenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "").strip() or str(raw["primary_provider"] or "").strip()
    if primary_provider not in _APPROVED_PROVIDERS:
        raise ValueError("provider_runtime_primary_provider_invalid")

    fallback_value = os.getenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS")
    fallbacks = _coerce_fallbacks(raw["fallback_providers"] if fallback_value is None else fallback_value)
    if any(provider not in _APPROVED_PROVIDERS for provider in fallbacks):
        raise ValueError("provider_runtime_fallback_provider_invalid")

    output_contract = os.getenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT", "").strip() or str(raw["output_contract"] or "").strip()
    if not output_contract:
        raise ValueError("provider_runtime_output_contract_invalid")

    timeout_value = os.getenv("PROVIDER_RUNTIME_TIMEOUT_MS")
    timeout_ms = _validate_timeout_ms(raw["timeout_ms"] if timeout_value is None else timeout_value.strip())

    return {
        "primary_provider": primary_provider,
        "fallbacks": fallbacks,
        "output_contract": output_contract,
        "timeout_ms": timeout_ms,
        "canary_percent": effective_canary_percent(raw["canary_percent"]),
    }


def _validate_timeout_ms(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("provider_runtime_timeout_ms_invalid")
    try:
        timeout_ms = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider_runtime_timeout_ms_invalid") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("provider_runtime_timeout_ms_invalid")
    if isinstance(value, str) and value.strip() != str(timeout_ms):
        raise ValueError("provider_runtime_timeout_ms_invalid")
    if not 500 <= timeout_ms <= 120000:
        raise ValueError("provider_runtime_timeout_ms_invalid")
    return timeout_ms


def _coerce_fallbacks(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    raise ValueError("provider_runtime_fallback_provider_invalid")


def _dedupe_providers(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _configuration_error_code(exc: Exception) -> str:
    candidate = str(exc).strip()
    return candidate if candidate in _CONFIGURATION_ERROR_CODES else "provider_runtime_configuration_invalid"


def _traffic_summary(traffic: ProviderTrafficSelection, additional: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = dict(additional or {})
    summary["traffic"] = traffic.safe_summary()
    return summary


def _unavailable(error_code: str, summary: dict[str, Any], *, elapsed_ms: int = 0) -> ProviderResult:
    result = ProviderResult.unavailable("router", error_code, elapsed_ms, fallback_allowed=False)
    result.raw_payload_safe_summary = summary
    return result


def _shadow_result(traffic: ProviderTrafficSelection, elapsed_ms: int, summary: dict[str, Any] | None = None) -> ProviderResult:
    return _unavailable("provider_shadow_only", summary or _traffic_summary(traffic), elapsed_ms=elapsed_ms)
