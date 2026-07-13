from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, text
from sqlalchemy.orm import Session

from .health import ProviderRuntimeHealth
from .output_contracts import OutputContracts
from .registry import ProviderRegistry
from .schemas import ProviderRequest, ProviderResult
from .traffic_selection import (
    TRAFFIC_SELECTION_SCHEMA,
    ProviderTrafficPath,
    ProviderTrafficSelection,
    effective_kill_switch,
    persisted_traffic_configuration_errors,
    select_provider_traffic,
)

logger = logging.getLogger(__name__)

_APPROVED_PROVIDER_ALIASES = frozenset({"private_ai_runtime"})
_APPROVED_OUTPUT_CONTRACTS = frozenset(
    {
        "nexus.ai_reply.v3",
        "nexus.ai_reply.v2",
        "nexus_webchat_runtime_reply_v1",
        "speedaf_ticket_triage_v1",
        "speedaf_delivery_exception_analysis_v1",
    }
)
_DIRECT_ENV_PROVIDERS = _APPROVED_PROVIDER_ALIASES
_PROVIDER_ALIAS_INVALID = "provider_runtime_provider_alias_invalid"
_OUTPUT_CONTRACT_INVALID = "provider_runtime_output_contract_invalid"
_TRAFFIC_CONFIGURATION_ERRORS = {
    "provider_runtime_traffic_mode_invalid",
    "provider_runtime_canary_percent_invalid",
    "provider_runtime_kill_switch_invalid",
    "provider_runtime_primary_provider_invalid",
    _PROVIDER_ALIAS_INVALID,
    _OUTPUT_CONTRACT_INVALID,
}
_FALLBACK_RESULTS = frozenset(
    {
        "blocked",
        "not_configured",
        "not_attempted",
        "pending",
        "succeeded",
        "failed",
    }
)
_FIXED_PROVIDER_AUDIT_ERRORS = frozenset({"provider_timeout"})


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
        safe_summary: dict | None,
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

    def reject_before_route(
        self,
        request: ProviderRequest,
        error_code: str,
    ) -> ProviderResult:
        """Persist bounded evidence for a request rejected before DB routing."""

        summary = _blocked_configuration_summary(error_code)
        self._write_audit(
            request,
            "traffic_select",
            "failed",
            "router",
            0,
            summary,
            error_code,
        )
        return _unavailable_with_traffic(error_code, None, summary=summary)

    @staticmethod
    def _parse_webchat_runtime_output(
        request: ProviderRequest,
        result: ProviderResult,
    ) -> dict:
        """Accept Runtime decision JSON before customer visibility."""

        from app.services.webchat_runtime_output_parser import (
            parse_runtime_reply_provider_output,
        )

        try:
            parsed_reply = parse_runtime_reply_provider_output(
                result.structured_output,
                evidence_present=request.tracking_fact_evidence_present,
            )
            parsed = dict(result.structured_output or {})
        except Exception:
            if (
                not request.tracking_fact_evidence_present
                or not isinstance(result.structured_output, dict)
            ):
                raise
            parsed = dict(result.structured_output or {})
            reply = parsed.get("customer_reply") or parsed.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise
            OutputContracts.check_security_rules(
                raw_output=json.dumps(parsed, ensure_ascii=False, default=str),
                parsed=parsed,
                evidence_present=True,
                request_body=request.body,
                knowledge_context=(request.metadata or {}).get("knowledge_context"),
            )
            parsed_reply = type(
                "_ParsedRuntimeFallback",
                (),
                {
                    "reply": reply.strip(),
                    "intent": parsed.get("intent") or "tracking",
                    "tracking_number": parsed.get("tracking_number"),
                    "handoff_required": bool(
                        parsed.get("handoff_required", False)
                    ),
                    "handoff_reason": parsed.get("handoff_reason"),
                    "recommended_agent_action": parsed.get(
                        "recommended_agent_action"
                    ),
                    "ai_decision": None,
                },
            )()

        parsed.setdefault("customer_reply", parsed_reply.reply)
        parsed.setdefault("reply", parsed_reply.reply)
        parsed.setdefault("language", "unknown")
        parsed.setdefault("intent", parsed_reply.intent)
        parsed.setdefault("tracking_number", parsed_reply.tracking_number)
        parsed.setdefault("handoff_required", parsed_reply.handoff_required)
        parsed.setdefault("handoff_reason", parsed_reply.handoff_reason)
        parsed.setdefault(
            "recommended_agent_action",
            parsed_reply.recommended_agent_action,
        )
        parsed.setdefault(
            "ticket_should_create",
            bool(parsed_reply.handoff_required),
        )
        if parsed_reply.ai_decision:
            parsed.setdefault("ai_decision", parsed_reply.ai_decision)
        OutputContracts.check_security_rules(
            raw_output=json.dumps(parsed, ensure_ascii=False, default=str),
            parsed=parsed,
            evidence_present=request.tracking_fact_evidence_present,
            request_body=request.body,
            knowledge_context=(request.metadata or {}).get("knowledge_context"),
        )
        return parsed

    async def route(self, request: ProviderRequest) -> ProviderResult:
        from . import bootstrap_provider_runtime

        bootstrap_provider_runtime()
        rule_statement = text(
            """
            SELECT primary_provider, fallback_providers, output_contract, timeout_ms, kill_switch, canary_percent
            FROM provider_routing_rules
            WHERE tenant_id = :tenant_id AND channel_key = :channel AND scenario = :scenario AND enabled = true
            """
        ).columns(kill_switch=Boolean())
        rule = self.db.execute(
            rule_statement,
            {
                "tenant_id": request.tenant_id,
                "channel": request.channel_key,
                "scenario": request.scenario,
            },
        ).mappings().first()

        if not rule:
            primary_provider = "private_ai_runtime"
            fallbacks: list[str] = []
            output_contract = "nexus_webchat_runtime_reply_v1"
            timeout_ms = 10000
            kill_switch: Any = False
            canary_percent: Any = 0
            persisted_provider_errors: list[str] = []
            persisted_output_contract_errors: list[str] = []
        else:
            primary_provider = rule["primary_provider"]
            raw_fallbacks = rule["fallback_providers"]
            persisted_provider_errors = persisted_provider_alias_errors(
                primary_provider=primary_provider,
                fallback_providers=raw_fallbacks,
            )
            try:
                fallbacks = _coerce_fallbacks(raw_fallbacks, strict=True)
            except ValueError:
                fallbacks = []
            output_contract = rule["output_contract"]
            persisted_output_contract_errors = persisted_output_contract_configuration_errors(
                output_contract
            )
            timeout_ms = rule["timeout_ms"]
            kill_switch = rule["kill_switch"]
            # Persisted NULL is corrupt configuration. Only the no-rule path
            # may safely default to control/0.
            canary_percent = rule["canary_percent"]

        persisted_canary_percent = canary_percent
        persisted_kill_switch = kill_switch

        try:
            if persisted_provider_errors:
                raise ValueError(persisted_provider_errors[0])
            if persisted_output_contract_errors:
                raise ValueError(persisted_output_contract_errors[0])

            (
                primary_provider,
                fallbacks,
                output_contract,
                timeout_ms,
                kill_switch,
                canary_percent,
            ) = _apply_env_overrides(
                primary_provider,
                fallbacks,
                output_contract,
                timeout_ms,
                kill_switch,
                canary_percent,
            )

            effective_provider_errors = persisted_provider_alias_errors(
                primary_provider=primary_provider,
                fallback_providers=fallbacks,
            )
            if effective_provider_errors:
                raise ValueError(effective_provider_errors[0])
            effective_output_contract_errors = persisted_output_contract_configuration_errors(
                output_contract
            )
            if effective_output_contract_errors:
                raise ValueError(effective_output_contract_errors[0])

            persisted_errors = persisted_traffic_configuration_errors(
                canary_percent=persisted_canary_percent,
                kill_switch=persisted_kill_switch,
            )
            if persisted_errors and not kill_switch:
                raise ValueError(persisted_errors[0])

            traffic = select_provider_traffic(
                request,
                canary_percent=canary_percent,
                kill_switch=kill_switch,
            )
            if persisted_errors:
                traffic = replace(
                    traffic,
                    configuration_errors=tuple(
                        dict.fromkeys(
                            (*traffic.configuration_errors, *persisted_errors)
                        )
                    ),
                )
        except (RuntimeError, TypeError, ValueError) as exc:
            error_code = _traffic_configuration_error_code(exc)
            summary = _blocked_configuration_summary(error_code)
            self._write_audit(
                request,
                "traffic_select",
                "failed",
                "router",
                0,
                summary,
                error_code,
            )
            return _unavailable_with_traffic(
                error_code,
                None,
                summary=summary,
            )

        if traffic.path == ProviderTrafficPath.KILL_SWITCH:
            summary = _summary_with_traffic(
                {},
                traffic,
                fallback_result="blocked",
            )
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                primary_provider,
                0,
                summary,
                "kill_switch_active",
            )
            return _unavailable_with_traffic(
                "kill_switch_active",
                traffic,
                summary=summary,
            )

        if traffic.path == ProviderTrafficPath.CONTROL:
            summary = _summary_with_traffic(
                {},
                traffic,
                fallback_result="not_attempted",
            )
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                primary_provider,
                0,
                summary,
                "provider_canary_control_path",
            )
            return _unavailable_with_traffic(
                "provider_canary_control_path",
                traffic,
                summary=summary,
            )

        request.output_contract = output_contract
        request.timeout_ms = timeout_ms
        providers_to_try = [primary_provider, *fallbacks]
        seen: set[str] = set()
        providers_to_try = [
            name
            for name in providers_to_try
            if name and not (name in seen or seen.add(name))
        ]
        shadow_only = traffic.path == ProviderTrafficPath.SHADOW_ONLY
        operation = "shadow_generate" if shadow_only else "generate"
        last_elapsed_ms = 0

        for index, provider_name in enumerate(providers_to_try):
            is_fallback = index > 0
            has_next = index + 1 < len(providers_to_try)

            health_decision = ProviderRuntimeHealth.should_skip(provider_name)
            if health_decision.skip:
                fallback_result = _failure_fallback_result(
                    is_fallback=is_fallback,
                    has_next=has_next,
                )
                self._write_audit(
                    request,
                    operation,
                    "skipped",
                    provider_name,
                    0,
                    _summary_with_traffic(
                        {"provider_health": health_decision.safe_summary()},
                        traffic,
                        fallback_result=fallback_result,
                    ),
                    health_decision.reason or "provider_health_skip",
                )
                continue

            adapter = ProviderRegistry.get(provider_name, self.db)
            if not adapter:
                fallback_result = _failure_fallback_result(
                    is_fallback=is_fallback,
                    has_next=has_next,
                )
                self._write_audit(
                    request,
                    operation,
                    "failed",
                    provider_name,
                    0,
                    _summary_with_traffic(
                        {},
                        traffic,
                        fallback_result=fallback_result,
                    ),
                    "adapter_not_registered",
                )
                continue

            result = await adapter.generate(self.db, request)
            last_elapsed_ms = result.elapsed_ms
            if not result.ok:
                fixed_error_code = _fixed_provider_error_code(result.error_code)
                fallback_result = (
                    "blocked"
                    if not result.fallback_allowed
                    else _failure_fallback_result(
                        is_fallback=is_fallback,
                        has_next=has_next,
                    )
                )
                safe_summary = _summary_with_traffic(
                    {"provider_result": "failed"},
                    traffic,
                    fallback_result=fallback_result,
                )
                if not shadow_only:
                    health_event = ProviderRuntimeHealth.record_failure(
                        provider_name,
                        fixed_error_code,
                    )
                    if health_event:
                        safe_summary["provider_health"] = health_event
                self._write_audit(
                    request,
                    operation,
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    safe_summary,
                    fixed_error_code,
                )
                if not result.fallback_allowed:
                    if shadow_only:
                        shadow_summary = _summary_with_traffic(
                            {"shadow_result": "failed"},
                            traffic,
                            fallback_result="blocked",
                        )
                        self._write_audit(
                            request,
                            "traffic_select",
                            "failed",
                            "router",
                            result.elapsed_ms,
                            shadow_summary,
                            "provider_shadow_failed",
                        )
                        return _unavailable_with_traffic(
                            "provider_shadow_failed",
                            traffic,
                            elapsed_ms=result.elapsed_ms,
                            summary=shadow_summary,
                            fallback_allowed=False,
                        )
                    return _unavailable_with_traffic(
                        fixed_error_code,
                        traffic,
                        elapsed_ms=result.elapsed_ms,
                        summary=safe_summary,
                        fallback_allowed=False,
                    )
                continue

            try:
                if not result.structured_output:
                    raise ValueError("No structured output provided")
                if (
                    request.scenario == "webchat_runtime_reply"
                    and output_contract == "nexus_webchat_runtime_reply_v1"
                ):
                    parsed = self._parse_webchat_runtime_output(request, result)
                else:
                    parsed = OutputContracts.validate_and_parse(
                        output_contract,
                        json.dumps(result.structured_output),
                        request.tracking_fact_evidence_present,
                        (request.metadata or {}).get("persona_context"),
                        request.body,
                        (request.metadata or {}).get("knowledge_context"),
                    )
                result.structured_output = parsed
                fallback_result = "succeeded" if is_fallback else "not_attempted"
                safe_summary = _summary_with_traffic(
                    {"provider_result": "succeeded"},
                    traffic,
                    fallback_result=fallback_result,
                )
                if not shadow_only:
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
                if shadow_only:
                    return _unavailable_with_traffic(
                        "provider_shadow_completed",
                        traffic,
                        elapsed_ms=result.elapsed_ms,
                        summary={
                            "shadow_result": "succeeded",
                            "fallback_result": fallback_result,
                        },
                    )
                return result
            except Exception:
                fallback_result = _failure_fallback_result(
                    is_fallback=is_fallback,
                    has_next=has_next,
                )
                safe_summary = _summary_with_traffic(
                    {"parse_error_code": "output_contract_rejected"},
                    traffic,
                    fallback_result=fallback_result,
                )
                if not shadow_only:
                    health_event = ProviderRuntimeHealth.record_failure(
                        provider_name,
                        "parse_reject",
                    )
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

        error_code = "provider_shadow_failed" if shadow_only else "all_providers_failed"
        summary = _summary_with_traffic(
            {},
            traffic,
            fallback_result="failed",
        )
        self._write_audit(
            request,
            operation,
            "failed",
            "router",
            last_elapsed_ms,
            summary,
            error_code,
        )
        return _unavailable_with_traffic(
            error_code,
            traffic,
            elapsed_ms=last_elapsed_ms,
            summary=summary,
        )


def persisted_provider_alias_errors(
    *,
    primary_provider: Any,
    fallback_providers: Any,
) -> list[str]:
    if not isinstance(primary_provider, str):
        return [_PROVIDER_ALIAS_INVALID]
    primary = primary_provider.strip()
    if primary != primary_provider or primary not in _APPROVED_PROVIDER_ALIASES:
        return [_PROVIDER_ALIAS_INVALID]

    try:
        fallbacks = _coerce_fallbacks(fallback_providers, strict=True)
    except ValueError:
        return [_PROVIDER_ALIAS_INVALID]
    if any(provider not in _APPROVED_PROVIDER_ALIASES for provider in fallbacks):
        return [_PROVIDER_ALIAS_INVALID]
    return []


def persisted_output_contract_configuration_errors(output_contract: Any) -> list[str]:
    if not isinstance(output_contract, str):
        return [_OUTPUT_CONTRACT_INVALID]
    normalized = output_contract.strip()
    if normalized != output_contract or normalized not in _APPROVED_OUTPUT_CONTRACTS:
        return [_OUTPUT_CONTRACT_INVALID]
    return []


def _traffic_configuration_error_code(exc: BaseException) -> str:
    value = str(exc).strip()
    if value in _TRAFFIC_CONFIGURATION_ERRORS:
        return value
    return "provider_runtime_traffic_configuration_invalid"


def _blocked_configuration_summary(error_code: str) -> dict[str, Any]:
    return {
        "fallback_result": "blocked",
        "traffic_selection": {
            "schema_version": TRAFFIC_SELECTION_SCHEMA,
            "configured_mode": "invalid",
            "configuration_errors": [error_code],
            "path": "control",
            "canary_percent": None,
            "bucket": None,
            "execute_candidate": False,
            "authoritative": False,
            "reason": error_code,
        },
    }


def _summary_with_traffic(
    summary: dict | None,
    traffic: ProviderTrafficSelection,
    *,
    fallback_result: str,
) -> dict:
    if fallback_result not in _FALLBACK_RESULTS:
        raise ValueError("provider_runtime_fallback_result_invalid")
    safe_summary = dict(summary or {})
    safe_summary["traffic_selection"] = traffic.safe_summary()
    safe_summary["fallback_result"] = fallback_result
    return safe_summary


def _failure_fallback_result(
    *,
    is_fallback: bool,
    has_next: bool,
) -> str:
    if has_next:
        return "pending"
    if is_fallback:
        return "failed"
    return "not_configured"


def _fixed_provider_error_code(value: str | None) -> str:
    """Map Adapter-controlled failures onto a closed audit enum.

    ProviderResult is an Adapter boundary and must not become an arbitrary
    Admin-visible error-code transport. Private runtime failures collapse to a
    fixed family code; all other unknown values collapse to the Router code.
    """

    if not isinstance(value, str):
        return "provider_runtime_provider_failed"
    normalized = value.strip().lower()
    if normalized in _FIXED_PROVIDER_AUDIT_ERRORS:
        return normalized
    if normalized.startswith("private_ai_runtime_"):
        return "private_ai_runtime_failed"
    return "provider_runtime_provider_failed"


def _unavailable_with_traffic(
    error_code: str,
    traffic: ProviderTrafficSelection | None,
    *,
    elapsed_ms: int = 0,
    summary: dict | None = None,
    fallback_allowed: bool = True,
) -> ProviderResult:
    result = ProviderResult.unavailable(
        "router",
        error_code,
        elapsed_ms,
        fallback_allowed=fallback_allowed,
    )
    safe_summary = dict(summary or {})
    safe_summary["unavailable"] = True
    if traffic is not None:
        safe_summary["traffic_selection"] = traffic.safe_summary()
    safe_summary.setdefault("fallback_result", "failed")
    result.raw_payload_safe_summary = safe_summary
    return result


def _apply_env_overrides(
    primary_provider: str,
    fallbacks: list[str],
    output_contract: str,
    timeout_ms: int,
    kill_switch: bool,
    canary_percent: Any,
) -> tuple[str, list[str], str, int, bool, object]:
    env_primary = os.getenv(
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER",
        "",
    ).strip()
    if env_primary:
        if env_primary not in _DIRECT_ENV_PROVIDERS:
            raise RuntimeError("provider_runtime_primary_provider_invalid")
        primary_provider = env_primary
        env_fallbacks = os.getenv(
            "PROVIDER_RUNTIME_FALLBACK_PROVIDERS",
            "",
        )
        if env_fallbacks:
            fallbacks = _coerce_fallbacks(env_fallbacks, strict=True)
    env_contract = os.getenv(
        "PROVIDER_RUNTIME_OUTPUT_CONTRACT",
        "",
    ).strip()
    if env_contract:
        output_contract = env_contract
    timeout_ms = _int_env(
        "PROVIDER_RUNTIME_TIMEOUT_MS",
        timeout_ms,
        minimum=500,
        maximum=120000,
    )

    kill_switch = effective_kill_switch(kill_switch)
    canary_override = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    if canary_override is not None:
        canary_percent = canary_override.strip()
    return (
        primary_provider,
        fallbacks,
        output_contract,
        timeout_ms,
        kill_switch,
        canary_percent,
    )


def _coerce_fallbacks(
    value: Any,
    *,
    strict: bool = False,
) -> list[str]:
    if value is None or value == "":
        return []
    parsed: Any = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError(_PROVIDER_ALIAS_INVALID) from exc
            return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(parsed, list):
        if strict:
            raise ValueError(_PROVIDER_ALIAS_INVALID)
        return []

    normalized: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            if strict:
                raise ValueError(_PROVIDER_ALIAS_INVALID)
            continue
        alias = item.strip()
        if not alias or alias != item:
            if strict:
                raise ValueError(_PROVIDER_ALIAS_INVALID)
            continue
        normalized.append(alias)
    return normalized


def _int_env(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))
