from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .health import ProviderRuntimeHealth
from .output_contracts import (
    AGENT_SPECIALIST_OUTPUT_CONTRACT,
    AGENT_TURN_OUTPUT_CONTRACT,
    OutputContracts,
)
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
        "provider_runtime_output_contract_mismatch",
        "provider_runtime_timeout_ms_invalid",
    }
)
_SAFE_PROVIDER_STRING_KEYS = frozenset(
    {
        "provider",
        "endpoint_path",
        "request_shape",
        "model",
        "prompt_profile",
        "ollama_keep_alive",
        "model_profile_key",
    }
)
_SAFE_PROVIDER_SCALAR_KEYS = frozenset(
    {
        "prompt_chars",
        "timeout_seconds",
        "elapsed_ms",
        "token_file_configured",
        "base_url_configured",
        "http_status",
        "retryable_http",
        "contract_repair_applied",
        "model_profile_version",
        "agent_release_id",
    }
)
_SAFE_PROVIDER_NUMERIC_MAP_KEYS = frozenset(
    {"usage", "runtime_usage", "ollama_options"}
)
_SAFE_PROVIDER_STRUCTURED_KEYS = frozenset({"context_compilation"})
_SAFE_PROVIDER_TOKEN = re.compile(r"^[A-Za-z0-9_./:+-]{1,160}$")


class ProviderRuntimeRouter:
    """Canonical Provider traffic, health, timeout and durable-audit authority."""

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
                    "safe_summary": json.dumps(
                        safe_summary or {},
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
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
            return self._reject_configuration(
                request,
                _configuration_error_code(exc),
            )

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
            requested_timeout_ms = _validate_timeout_ms(
                request.timeout_ms or config["timeout_ms"]
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return self._reject_configuration(
                request,
                _configuration_error_code(exc),
            )

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
            summary = _traffic_summary(
                traffic,
                {"configuration_error": "provider_runtime_output_contract_invalid"},
            )
            self._write_audit(
                request,
                "traffic_select",
                "failed",
                "router",
                0,
                summary,
                "provider_runtime_output_contract_invalid",
            )
            return _unavailable(
                "provider_runtime_output_contract_invalid",
                summary,
            )

        request.output_contract = output_contract
        request.timeout_ms = min(requested_timeout_ms, config["timeout_ms"])
        providers = _dedupe_providers(
            [config["primary_provider"], *config["fallbacks"]]
        )
        operation = "generate"
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
                    _traffic_summary(
                        traffic,
                        {"provider_health": health_decision.safe_summary()},
                    ),
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
                error_code = _bounded_provider_error_code(result.error_code)
                safe_summary = _traffic_summary(
                    traffic,
                    {
                        **_bounded_provider_summary(
                            result.raw_payload_safe_summary
                        ),
                        "provider_error_category": error_code,
                        "effective_timeout_ms": request.timeout_ms,
                    },
                )
                health_event = ProviderRuntimeHealth.record_failure(
                    provider_name,
                    error_code,
                )
                if health_event:
                    safe_summary["provider_health"] = health_event
                result.error_code = error_code
                result.raw_payload_safe_summary = safe_summary
                self._write_audit(
                    request,
                    operation,
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    safe_summary,
                    error_code,
                )
                if not result.fallback_allowed:
                    return result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("provider_output_missing")
                parsed = OutputContracts.validate_and_parse(
                    output_contract,
                    json.dumps(
                        result.structured_output,
                        ensure_ascii=False,
                    ),
                )
            except Exception:
                safe_summary = _traffic_summary(
                    traffic,
                    {
                        "parse_error": "provider_output_rejected",
                        "effective_timeout_ms": request.timeout_ms,
                    },
                )
                health_event = ProviderRuntimeHealth.record_failure(
                    provider_name,
                    "provider_output_invalid",
                )
                if health_event:
                    safe_summary["provider_health"] = health_event
                self._write_audit(
                    request,
                    "parse_reject",
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    safe_summary,
                    "provider_output_invalid",
                )
                continue

            result.structured_output = parsed
            safe_summary = _traffic_summary(
                traffic,
                {
                    **_bounded_provider_summary(
                        result.raw_payload_safe_summary
                    ),
                    "effective_timeout_ms": request.timeout_ms,
                },
            )
            health_event = ProviderRuntimeHealth.record_success(provider_name)
            if health_event:
                safe_summary["provider_health"] = health_event
            result.raw_payload_safe_summary = safe_summary
            self._write_audit(
                request,
                operation,
                "ok",
                provider_name,
                result.elapsed_ms,
                safe_summary,
            )
            return result

        summary = _traffic_summary(
            traffic,
            {"effective_timeout_ms": request.timeout_ms},
        )
        self._write_audit(
            request,
            operation,
            "failed",
            "router",
            last_elapsed_ms,
            summary,
            "all_providers_failed",
        )
        return _unavailable(
            "all_providers_failed",
            summary,
            elapsed_ms=last_elapsed_ms,
        )

    def _reject_configuration(
        self,
        request: ProviderRequest,
        reason: str,
    ) -> ProviderResult:
        summary = {
            "traffic": {
                "path": "blocked",
                "authoritative": False,
                "execute_candidate": False,
                "reason": reason,
            }
        }
        self._write_audit(
            request,
            "traffic_select",
            "failed",
            "router",
            0,
            summary,
            reason,
        )
        error_code = (
            reason
            if reason
            in {
                "provider_runtime_output_contract_invalid",
                "provider_runtime_output_contract_mismatch",
            }
            else "provider_runtime_configuration_invalid"
        )
        return _unavailable(error_code, summary)


def _load_rule(db: Session, request: ProviderRequest) -> dict[str, Any]:
    requested_contract = str(
        request.output_contract or AGENT_TURN_OUTPUT_CONTRACT
    ).strip()
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
            "output_contract": requested_contract,
            "requested_output_contract": requested_contract,
            "rule_found": False,
            "timeout_ms": 15000,
            "kill_switch": False,
            "canary_percent": 0,
        }
    return {
        "primary_provider": rule["primary_provider"],
        "fallback_providers": rule["fallback_providers"],
        "output_contract": rule["output_contract"],
        "requested_output_contract": requested_contract,
        "rule_found": True,
        "timeout_ms": rule["timeout_ms"],
        "kill_switch": rule["kill_switch"],
        "canary_percent": rule["canary_percent"],
    }


def _effective_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    primary_provider = (
        os.getenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "").strip()
        or str(raw["primary_provider"] or "").strip()
    )
    if primary_provider not in _APPROVED_PROVIDERS:
        raise ValueError("provider_runtime_primary_provider_invalid")

    fallback_value = os.getenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS")
    fallbacks = _coerce_fallbacks(
        raw["fallback_providers"]
        if fallback_value is None
        else fallback_value
    )
    if any(provider not in _APPROVED_PROVIDERS for provider in fallbacks):
        raise ValueError("provider_runtime_fallback_provider_invalid")

    requested_contract = str(
        raw.get("requested_output_contract") or AGENT_TURN_OUTPUT_CONTRACT
    ).strip()
    configured_contract = str(raw.get("output_contract") or "").strip()
    if requested_contract == AGENT_SPECIALIST_OUTPUT_CONTRACT:
        if raw.get("rule_found") and configured_contract != requested_contract:
            raise ValueError("provider_runtime_output_contract_mismatch")
        output_contract = requested_contract
    else:
        output_contract = (
            os.getenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT", "").strip()
            or configured_contract
            or requested_contract
        )
    if not output_contract or not OutputContracts.get_schema(output_contract):
        raise ValueError("provider_runtime_output_contract_invalid")

    timeout_value = os.getenv("PROVIDER_RUNTIME_TIMEOUT_MS")
    timeout_ms = _validate_timeout_ms(
        raw["timeout_ms"]
        if timeout_value is None
        else timeout_value.strip()
    )
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
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [
                item.strip()
                for item in value.split(",")
                if item.strip()
            ]
        if isinstance(parsed, list):
            return [
                str(item).strip()
                for item in parsed
                if str(item).strip()
            ]
    raise ValueError("provider_runtime_fallback_provider_invalid")


def _dedupe_providers(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _configuration_error_code(exc: Exception) -> str:
    candidate = str(exc).strip()
    return (
        candidate
        if candidate in _CONFIGURATION_ERROR_CODES
        else "provider_runtime_configuration_invalid"
    )


def _bounded_provider_error_code(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if "timeout" in candidate:
        return "provider_timeout"
    if "_http_" in candidate:
        return "provider_http_error"
    if any(
        marker in candidate
        for marker in ("network", "url_error", "connection")
    ):
        return "provider_network_error"
    if any(
        marker in candidate
        for marker in (
            "disabled",
            "missing",
            "invalid",
            "forbidden",
            "requires",
        )
    ):
        return "provider_configuration_error"
    if any(
        marker in candidate
        for marker in (
            "bad",
            "empty",
            "contract",
            "response",
            "json",
            "schema",
        )
    ):
        return "provider_output_invalid"
    return "provider_call_failed"


def _bounded_provider_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in _SAFE_PROVIDER_STRING_KEYS:
        value = raw.get(key)
        if (
            isinstance(value, str)
            and _SAFE_PROVIDER_TOKEN.fullmatch(value.strip())
        ):
            safe[key] = value.strip()
    for key in _SAFE_PROVIDER_SCALAR_KEYS:
        value = raw.get(key)
        if isinstance(value, (bool, int)):
            safe[key] = value
        elif isinstance(value, float) and math.isfinite(value):
            safe[key] = value
    for key in _SAFE_PROVIDER_NUMERIC_MAP_KEYS:
        value = _bounded_numeric_map(raw.get(key))
        if value:
            safe[key] = value
    for key in _SAFE_PROVIDER_STRUCTURED_KEYS:
        value = _bounded_safe_structure(raw.get(key))
        if value:
            safe[key] = value
    return safe


def _bounded_numeric_map(
    value: Any,
    *,
    depth: int = 0,
) -> dict[str, Any]:
    if not isinstance(value, dict) or depth > 2:
        return {}
    safe: dict[str, Any] = {}
    for raw_key, item in list(value.items())[:32]:
        key = str(raw_key).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", key):
            continue
        if isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int):
            safe[key] = item
        elif isinstance(item, float) and math.isfinite(item):
            safe[key] = item
        elif isinstance(item, dict):
            nested = _bounded_numeric_map(item, depth=depth + 1)
            if nested:
                safe[key] = nested
    return safe


def _bounded_safe_structure(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, list):
        return [
            item
            for raw in value[:32]
            if (
                item := _bounded_safe_structure(
                    raw,
                    depth=depth + 1,
                )
            )
            is not None
        ]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, raw_item in list(value.items())[:48]:
            key = str(raw_key).strip()[:80]
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", key):
                continue
            item = _bounded_safe_structure(
                raw_item,
                depth=depth + 1,
            )
            if item is not None:
                output[key] = item
        return output
    return None


def _traffic_summary(
    traffic: ProviderTrafficSelection,
    additional: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(additional or {})
    summary["traffic"] = traffic.safe_summary()
    return summary


def _unavailable(
    error_code: str,
    summary: dict[str, Any],
    *,
    elapsed_ms: int = 0,
) -> ProviderResult:
    result = ProviderResult.unavailable(
        "router",
        error_code,
        elapsed_ms,
        fallback_allowed=False,
    )
    result.raw_payload_safe_summary = summary
    return result
