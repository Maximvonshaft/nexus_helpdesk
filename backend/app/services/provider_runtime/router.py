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

_DIRECT_ENV_PROVIDERS = frozenset({"private_ai_runtime"})
_CONFIGURATION_ERROR_CODES = frozenset(
    {
        "provider_runtime_traffic_mode_invalid",
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_kill_switch_invalid",
        "provider_runtime_primary_provider_invalid",
        "provider_runtime_fallback_provider_invalid",
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
            # Provider payloads and exception strings are intentionally excluded.
            logger.error("provider_runtime_audit_write_failed")
            self.db.rollback()

    @staticmethod
    def _parse_webchat_runtime_output(
        request: ProviderRequest,
        result: ProviderResult,
    ) -> dict[str, Any]:
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
                    "handoff_required": bool(parsed.get("handoff_required", False)),
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
        rule = self.db.execute(
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

        if not rule:
            # Absence of a persisted routing rule is a control path, never an
            # implicit authorization to send all traffic to a Provider.
            primary_provider = "private_ai_runtime"
            fallbacks: list[str] = []
            output_contract = WEBCHAT_RUNTIME_OUTPUT_CONTRACT
            timeout_ms = 10000
            kill_switch: Any = False
            canary_percent: Any = 0
        else:
            primary_provider = str(rule["primary_provider"] or "").strip()
            fallbacks = _coerce_fallbacks(rule["fallback_providers"])
            output_contract = str(rule["output_contract"] or "").strip()
            timeout_ms = rule["timeout_ms"]
            kill_switch = rule["kill_switch"]
            canary_percent = rule["canary_percent"]

        try:
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
            traffic = select_provider_traffic(
                request,
                canary_percent=canary_percent,
                kill_switch=kill_switch,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            error_code = _configuration_error_code(exc)
            summary = {
                "traffic": {
                    "path": "blocked",
                    "authoritative": False,
                    "execute_candidate": False,
                    "reason": error_code,
                }
            }
            self._write_audit(
                request,
                "traffic_select",
                "failed",
                "router",
                0,
                summary,
                error_code,
            )
            return _unavailable_with_summary(
                "provider_runtime_configuration_invalid",
                summary,
            )

        if traffic.path == ProviderTrafficPath.KILL_SWITCH:
            summary = _traffic_summary(traffic)
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                primary_provider,
                0,
                summary,
                "kill_switch_active",
            )
            return _unavailable_with_summary("kill_switch_active", summary)

        if traffic.path == ProviderTrafficPath.CONTROL:
            summary = _traffic_summary(traffic)
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                primary_provider,
                0,
                summary,
                "provider_canary_control_path",
            )
            return _unavailable_with_summary(
                "provider_canary_control_path",
                summary,
            )

        request.output_contract = output_contract
        request.timeout_ms = timeout_ms
        providers_to_try = _dedupe_providers([primary_provider, *fallbacks])
        shadow_only = traffic.path == ProviderTrafficPath.SHADOW_ONLY
        operation = "shadow_generate" if shadow_only else "generate"
        last_elapsed_ms = 0

        for provider_name in providers_to_try:
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
            if not adapter:
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
                safe_summary = dict(result.raw_payload_safe_summary or {})
                health_event = ProviderRuntimeHealth.record_failure(
                    provider_name,
                    result.error_code,
                )
                if health_event:
                    safe_summary["provider_health"] = health_event
                safe_summary = _traffic_summary(traffic, safe_summary)
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
                    return (
                        _shadow_result(traffic, result.elapsed_ms, safe_summary)
                        if shadow_only
                        else result
                    )
                continue

            try:
                if not result.structured_output:
                    raise ValueError("No structured output provided")
                if (
                    request.scenario == "webchat_runtime_reply"
                    and output_contract == WEBCHAT_RUNTIME_OUTPUT_CONTRACT
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
                safe_summary = dict(result.raw_payload_safe_summary or {})
                health_event = ProviderRuntimeHealth.record_success(provider_name)
                if health_event:
                    safe_summary["provider_health"] = health_event
                safe_summary = _traffic_summary(traffic, safe_summary)
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
                    # Shadow execution never returns candidate text or decision
                    # authority to the caller.
                    return _shadow_result(
                        traffic,
                        result.elapsed_ms,
                        safe_summary,
                    )
                return result
            except Exception:
                safe_summary = _traffic_summary(
                    traffic,
                    {"parse_error": "provider_output_rejected"},
                )
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
        return _unavailable_with_summary(
            "all_providers_failed",
            summary,
            elapsed_ms=last_elapsed_ms,
        )


def _traffic_summary(
    traffic: ProviderTrafficSelection,
    additional: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(additional or {})
    summary["traffic"] = traffic.safe_summary()
    return summary


def _unavailable_with_summary(
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


def _shadow_result(
    traffic: ProviderTrafficSelection,
    elapsed_ms: int,
    summary: dict[str, Any] | None = None,
) -> ProviderResult:
    return _unavailable_with_summary(
        "provider_shadow_only",
        summary or _traffic_summary(traffic),
        elapsed_ms=elapsed_ms,
    )


def _apply_env_overrides(
    primary_provider: str,
    fallbacks: list[str],
    output_contract: str,
    timeout_ms: Any,
    kill_switch: Any,
    canary_percent: Any,
) -> tuple[str, list[str], str, int, bool, int]:
    env_primary = os.getenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "").strip()
    if env_primary:
        primary_provider = env_primary

    if primary_provider not in _DIRECT_ENV_PROVIDERS:
        raise ValueError("provider_runtime_primary_provider_invalid")

    env_fallbacks = os.getenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS")
    if env_fallbacks is not None:
        fallbacks = _coerce_fallbacks(env_fallbacks)

    for provider in fallbacks:
        if provider not in _DIRECT_ENV_PROVIDERS:
            raise ValueError("provider_runtime_fallback_provider_invalid")

    env_contract = os.getenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT", "").strip()
    if env_contract:
        output_contract = env_contract
    if not output_contract:
        raise ValueError("provider_runtime_output_contract_invalid")

    timeout_ms = _int_env(
        "PROVIDER_RUNTIME_TIMEOUT_MS",
        timeout_ms,
        minimum=500,
        maximum=120000,
    )
    canary_percent = effective_canary_percent(canary_percent)
    kill_switch = effective_kill_switch(kill_switch)
    return (
        primary_provider,
        fallbacks,
        output_contract,
        timeout_ms,
        kill_switch,
        canary_percent,
    )


def _coerce_fallbacks(value: Any) -> list[str]:
    if not value:
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
    seen: set[str] = set()
    return [
        value
        for value in values
        if value and not (value in seen or seen.add(value))
    ]


def _configuration_error_code(exc: Exception) -> str:
    candidate = str(exc).strip()
    return (
        candidate
        if candidate in _CONFIGURATION_ERROR_CODES
        else "provider_runtime_configuration_invalid"
    )


def _int_env(
    name: str,
    default: Any,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name.lower()}_invalid") from exc
    return max(minimum, min(value, maximum))
