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
from .output_contracts import OutputContracts
from .registry import ProviderRegistry
from .schemas import ProviderRequest, ProviderResult
from .traffic_selection import ProviderTrafficPath, ProviderTrafficSelection, select_provider_traffic

logger = logging.getLogger(__name__)

_DIRECT_ENV_PROVIDERS = {"private_ai_runtime"}


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
                    "safe_summary": json.dumps(safe_summary or {}),
                    "error_code": error_code,
                    "elapsed_ms": elapsed_ms,
                    "now": datetime.now(timezone.utc),
                },
            )
            self.db.commit()
        except Exception as exc:
            logger.error(
                "provider_runtime_audit_write_failed",
                extra={"error_type": type(exc).__name__},
            )
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

    @staticmethod
    def _traffic_summary(
        selection: ProviderTrafficSelection,
        safe_summary: dict[str, Any] | None = None,
        *,
        fallback_result: str,
        attempt_index: int | None = None,
    ) -> dict[str, Any]:
        summary = dict(safe_summary or {})
        summary["traffic_selection"] = selection.safe_summary(fallback_result=fallback_result)
        if attempt_index is not None:
            summary["provider_attempt_index"] = attempt_index
        return summary

    async def route(self, request: ProviderRequest) -> ProviderResult:
        from . import bootstrap_provider_runtime

        bootstrap_provider_runtime()
        rule = self.db.execute(
            text(
                """
                SELECT primary_provider, fallback_providers, output_contract, timeout_ms, kill_switch, canary_percent
                FROM provider_routing_rules
                WHERE tenant_id = :tenant_id AND channel_key = :channel AND scenario = :scenario AND enabled = true
                """
            ),
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
            default_mode = "control"
        else:
            primary_provider = rule["primary_provider"]
            fallbacks = _coerce_fallbacks(rule["fallback_providers"])
            output_contract = rule["output_contract"]
            timeout_ms = rule["timeout_ms"]
            kill_switch = rule["kill_switch"]
            canary_percent = rule["canary_percent"]
            default_mode = "canary"

        selection = select_provider_traffic(
            request,
            canary_percent=canary_percent,
            kill_switch=kill_switch,
            configured_mode_value=default_mode,
        )

        if selection.path is ProviderTrafficPath.KILL_SWITCH:
            self._write_audit(
                request,
                "generate",
                "skipped",
                primary_provider,
                0,
                self._traffic_summary(selection, fallback_result="suppressed_by_kill_switch"),
                "kill_switch_active",
            )
            return ProviderResult.unavailable("router", "kill_switch_active", 0, fallback_allowed=False)

        if not selection.execute_candidate:
            error_code = (
                "provider_runtime_traffic_configuration_invalid"
                if selection.configuration_errors
                else "provider_runtime_control_path"
            )
            self._write_audit(
                request,
                "generate",
                "skipped",
                primary_provider,
                0,
                self._traffic_summary(selection, fallback_result="candidate_not_selected"),
                error_code,
            )
            return ProviderResult.unavailable("router", error_code, 0, fallback_allowed=False)

        try:
            primary_provider, fallbacks, output_contract, timeout_ms = _apply_env_overrides(
                primary_provider,
                fallbacks,
                output_contract,
                timeout_ms,
            )
        except RuntimeError:
            self._write_audit(
                request,
                "generate",
                "failed",
                "router",
                0,
                self._traffic_summary(
                    selection,
                    {"provider_configuration_valid": False},
                    fallback_result="provider_configuration_invalid",
                ),
                "provider_runtime_primary_provider_invalid",
            )
            return ProviderResult.unavailable(
                "router",
                "provider_runtime_primary_provider_invalid",
                0,
                fallback_allowed=False,
            )

        fallbacks = [provider for provider in fallbacks if provider in _DIRECT_ENV_PROVIDERS]
        request.output_contract = output_contract
        request.timeout_ms = timeout_ms
        providers_to_try = [primary_provider] + list(fallbacks)
        seen: set[str] = set()
        providers_to_try = [name for name in providers_to_try if name and not (name in seen or seen.add(name))]

        for attempt_index, provider_name in enumerate(providers_to_try):
            health_decision = ProviderRuntimeHealth.should_skip(provider_name)
            if health_decision.skip:
                self._write_audit(
                    request,
                    "generate",
                    "skipped",
                    provider_name,
                    0,
                    self._traffic_summary(
                        selection,
                        {"provider_health": health_decision.safe_summary()},
                        fallback_result="health_skip",
                        attempt_index=attempt_index,
                    ),
                    health_decision.reason or "provider_health_skip",
                )
                continue

            adapter = ProviderRegistry.get(provider_name, self.db)
            if not adapter:
                self._write_audit(
                    request,
                    "generate",
                    "failed",
                    provider_name,
                    0,
                    self._traffic_summary(
                        selection,
                        fallback_result="adapter_not_registered",
                        attempt_index=attempt_index,
                    ),
                    "adapter_not_registered",
                )
                continue

            result = await adapter.generate(self.db, request)
            if not result.ok:
                safe_summary = dict(result.raw_payload_safe_summary or {})
                health_event = ProviderRuntimeHealth.record_failure(provider_name, result.error_code)
                if health_event:
                    safe_summary["provider_health"] = health_event
                    result.raw_payload_safe_summary = safe_summary
                self._write_audit(
                    request,
                    "generate",
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    self._traffic_summary(
                        selection,
                        safe_summary,
                        fallback_result="fallback_allowed" if result.fallback_allowed else "fallback_blocked",
                        attempt_index=attempt_index,
                    ),
                    result.error_code,
                )
                if not result.fallback_allowed:
                    return result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("provider_runtime_structured_output_missing")
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
                safe_summary = dict(result.raw_payload_safe_summary or {})
                health_event = ProviderRuntimeHealth.record_success(provider_name)
                if health_event:
                    safe_summary["provider_health"] = health_event
                    result.raw_payload_safe_summary = safe_summary
                if selection.path is ProviderTrafficPath.SHADOW_ONLY:
                    self._write_audit(
                        request,
                        "generate",
                        "shadow_ok",
                        provider_name,
                        result.elapsed_ms,
                        self._traffic_summary(
                            selection,
                            safe_summary,
                            fallback_result="shadow_output_discarded",
                            attempt_index=attempt_index,
                        ),
                    )
                    return ProviderResult.unavailable(
                        "router",
                        "provider_runtime_shadow_only",
                        result.elapsed_ms,
                        fallback_allowed=False,
                    )
                self._write_audit(
                    request,
                    "generate",
                    "ok",
                    provider_name,
                    result.elapsed_ms,
                    self._traffic_summary(
                        selection,
                        safe_summary,
                        fallback_result="primary_succeeded" if attempt_index == 0 else "fallback_succeeded",
                        attempt_index=attempt_index,
                    ),
                )
                return result
            except Exception:
                safe_summary: dict[str, Any] = {"parse_reject": True}
                health_event = ProviderRuntimeHealth.record_failure(provider_name, "parse_reject")
                if health_event:
                    safe_summary["provider_health"] = health_event
                self._write_audit(
                    request,
                    "parse_reject",
                    "failed",
                    provider_name,
                    result.elapsed_ms,
                    self._traffic_summary(
                        selection,
                        safe_summary,
                        fallback_result="parse_reject",
                        attempt_index=attempt_index,
                    ),
                    "parse_reject",
                )
                continue

        final_error = (
            "provider_runtime_shadow_failed"
            if selection.path is ProviderTrafficPath.SHADOW_ONLY
            else "all_providers_failed"
        )
        self._write_audit(
            request,
            "generate",
            "failed",
            "router",
            0,
            self._traffic_summary(selection, fallback_result="providers_exhausted"),
            final_error,
        )
        return ProviderResult.unavailable("router", final_error, 0, fallback_allowed=False)


def _apply_env_overrides(
    primary_provider: str,
    fallbacks: list[str],
    output_contract: str,
    timeout_ms: int,
) -> tuple[str, list[str], str, int]:
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
    return primary_provider, fallbacks, output_contract, timeout_ms


def _coerce_fallbacks(value: Any) -> list[str]:
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
