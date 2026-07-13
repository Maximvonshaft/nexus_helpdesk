from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone

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

_DIRECT_ENV_PROVIDERS = {"private_ai_runtime"}
_TRAFFIC_CONFIGURATION_ERRORS = {
    "provider_runtime_traffic_mode_invalid",
    "provider_runtime_canary_percent_invalid",
    "provider_runtime_kill_switch_invalid",
}
_OUTPUT_CONTRACT_REJECTED = "output_contract_rejected"


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
    ):
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
                    "safe_summary": json.dumps(safe_summary or {}),
                    "error_code": error_code,
                    "elapsed_ms": elapsed_ms,
                    "now": datetime.now(timezone.utc),
                },
            )
            self.db.commit()
        except Exception as exc:
            logger.error("provider_runtime_audit_write_failed", extra={"error": str(exc)})
            self.db.rollback()

    @staticmethod
    def _parse_webchat_runtime_output(request: ProviderRequest, result: ProviderResult) -> dict:
        """Accept Runtime decision JSON before customer visibility."""

        from app.services.webchat_runtime_output_parser import parse_runtime_reply_provider_output

        try:
            parsed_reply = parse_runtime_reply_provider_output(
                result.structured_output,
                evidence_present=request.tracking_fact_evidence_present,
            )
            parsed = dict(result.structured_output or {})
        except Exception:
            if not request.tracking_fact_evidence_present or not isinstance(result.structured_output, dict):
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
                    "recommended_agent_action": parsed.get("recommended_agent_action"),
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
        parsed.setdefault("recommended_agent_action", parsed_reply.recommended_agent_action)
        parsed.setdefault("ticket_should_create", bool(parsed_reply.handoff_required))
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
            fallbacks = []
            output_contract = "nexus_webchat_runtime_reply_v1"
            timeout_ms = 10000
            kill_switch = False
            canary_percent = 0
        else:
            primary_provider = rule["primary_provider"]
            fallbacks = _coerce_fallbacks(rule["fallback_providers"])
            output_contract = rule["output_contract"]
            timeout_ms = rule["timeout_ms"]
            kill_switch = rule["kill_switch"]
            canary_percent = 0 if rule["canary_percent"] is None else rule["canary_percent"]

        persisted_canary_percent = canary_percent
        persisted_kill_switch = kill_switch

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
            fallbacks = [provider for provider in fallbacks if provider in _DIRECT_ENV_PROVIDERS]
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
                        dict.fromkeys((*traffic.configuration_errors, *persisted_errors))
                    ),
                )
        except (RuntimeError, TypeError, ValueError) as exc:
            error_code = _traffic_configuration_error_code(exc)
            summary = {
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
                }
            }
            self._write_audit(
                request,
                "traffic_select",
                "failed",
                primary_provider,
                0,
                summary,
                error_code,
            )
            return _unavailable_with_traffic(error_code, None, summary=summary)

        if traffic.path == ProviderTrafficPath.KILL_SWITCH:
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                primary_provider,
                0,
                _summary_with_traffic({}, traffic),
                "kill_switch_active",
            )
            return _unavailable_with_traffic("kill_switch_active", traffic)

        if traffic.path == ProviderTrafficPath.CONTROL:
            self._write_audit(
                request,
                "traffic_select",
                "skipped",
                primary_provider,
                0,
                _summary_with_traffic({}, traffic),
                "provider_canary_control_path",
            )
            return _unavailable_with_traffic("provider_canary_control_path", traffic)

        request.output_contract = output_contract
        request.timeout_ms = timeout_ms
        providers_to_try = [primary_provider, *fallbacks]
        seen: set[str] = set()
        providers_to_try = [name for name in providers_to_try if name and not (name in seen or seen.add(name))]
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
                    _summary_with_traffic({"provider_health": health_decision.safe_summary()}, traffic),
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
                    _summary_with_traffic({}, traffic),
                    "adapter_not_registered",
                )
                continue

            result = await adapter.generate(self.db, request)
            last_elapsed_ms = result.elapsed_ms
            if not result.ok:
                safe_summary = _summary_with_traffic(dict(result.raw_payload_safe_summary or {}), traffic)
                if not shadow_only:
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
                if not shadow_only and not result.fallback_allowed:
                    return result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("No structured output provided")
                if request.scenario == "webchat_runtime_reply" and output_contract == "nexus_webchat_runtime_reply_v1":
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
                safe_summary = _summary_with_traffic(dict(result.raw_payload_safe_summary or {}), traffic)
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
                        summary={"shadow_provider": provider_name, "shadow_result": "valid"},
                    )
                return result
            except Exception:
                safe_summary = _summary_with_traffic(
                    {"parse_error_code": _OUTPUT_CONTRACT_REJECTED},
                    traffic,
                )
                if not shadow_only:
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

        error_code = "provider_shadow_failed" if shadow_only else "all_providers_failed"
        self._write_audit(
            request,
            operation,
            "failed",
            "router",
            last_elapsed_ms,
            _summary_with_traffic({}, traffic),
            error_code,
        )
        return _unavailable_with_traffic(error_code, traffic, elapsed_ms=last_elapsed_ms)


def _traffic_configuration_error_code(exc: BaseException) -> str:
    value = str(exc).strip()
    if value in _TRAFFIC_CONFIGURATION_ERRORS:
        return value
    return "provider_runtime_traffic_configuration_invalid"


def _summary_with_traffic(summary: dict | None, traffic: ProviderTrafficSelection) -> dict:
    safe_summary = dict(summary or {})
    safe_summary["traffic_selection"] = traffic.safe_summary()
    return safe_summary


def _unavailable_with_traffic(
    error_code: str,
    traffic: ProviderTrafficSelection | None,
    *,
    elapsed_ms: int = 0,
    summary: dict | None = None,
) -> ProviderResult:
    result = ProviderResult.unavailable("router", error_code, elapsed_ms)
    safe_summary = dict(summary or {})
    safe_summary["unavailable"] = True
    if traffic is not None:
        safe_summary["traffic_selection"] = traffic.safe_summary()
    result.raw_payload_safe_summary = safe_summary
    return result


def _apply_env_overrides(
    primary_provider: str,
    fallbacks: list[str],
    output_contract: str,
    timeout_ms: int,
    kill_switch: bool,
    canary_percent,
) -> tuple[str, list[str], str, int, bool, object]:
    env_primary = os.getenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "").strip()
    if env_primary:
        if env_primary not in _DIRECT_ENV_PROVIDERS:
            raise RuntimeError("provider_runtime_primary_provider_invalid")
        primary_provider = env_primary
        env_fallbacks = os.getenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "")
        if env_fallbacks:
            fallbacks = _coerce_fallbacks(env_fallbacks)
    env_contract = os.getenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT", "").strip()
    if env_contract:
        output_contract = env_contract
    timeout_ms = _int_env("PROVIDER_RUNTIME_TIMEOUT_MS", timeout_ms, minimum=500, maximum=120000)

    kill_switch = effective_kill_switch(kill_switch)
    canary_override = os.getenv("PROVIDER_RUNTIME_CANARY_PERCENT")
    if canary_override is not None:
        canary_percent = canary_override.strip()
    return primary_provider, fallbacks, output_contract, timeout_ms, kill_switch, canary_percent


def _coerce_fallbacks(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    return []


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))
