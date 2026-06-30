from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .health import ProviderRuntimeHealth
from .output_contracts import OutputContracts
from .registry import ProviderRegistry
from .schemas import ProviderRequest, ProviderResult

logger = logging.getLogger(__name__)

_DIRECT_ENV_PROVIDERS = {"codex_app_server", "codex_direct", "openai_responses", "rule_engine"}
_REMOVED_PROVIDERS = {"external_channel_responses"}


class ProviderRuntimeRouter:
    def __init__(self, db: Session):
        self.db = db

    def _write_audit(self, request: ProviderRequest, operation: str, status: str, provider: str, elapsed_ms: int, safe_summary: dict | None, error_code: str | None = None):
        try:
            self.db.execute(text("""
                INSERT INTO provider_runtime_audit_logs
                (id, tenant_id, provider, request_id, channel_key, session_id, operation, status, safe_summary, error_code, elapsed_ms, created_at)
                VALUES (:id, :tenant_id, :provider, :request_id, :channel_key, :session_id, :operation, :status, :safe_summary, :error_code, :elapsed_ms, :now)
            """), {
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
            })
            self.db.commit()
        except Exception as exc:
            logger.error("provider_runtime_audit_write_failed", extra={"error": str(exc)})
            self.db.rollback()

    @staticmethod
    def _stable_percent_bucket(tenant_id: str, channel_key: str, session_id: str) -> int:
        raw = f"{tenant_id}:{channel_key}:{session_id}"
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
        return int(digest[:8], 16) % 100

    @staticmethod
    def _parse_webchat_fast_output(request: ProviderRequest, result: ProviderResult) -> dict:
        """Accept legacy fast-reply JSON and the new AI decision JSON.

        ProviderRuntime historically enforced speedaf_webchat_fast_reply_v1 with
        additionalProperties=false. WebChat fast now owns final tool execution,
        but router-level security still rejects leaked internals, secrets, and
        unsupported live tracking status claims before customer visibility.
        """

        from app.services.webchat_fast_output_parser import parse_fast_reply_provider_output

        parsed_reply = parse_fast_reply_provider_output(result.structured_output)
        parsed = dict(result.structured_output or {})
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
        rule = self.db.execute(text("""
            SELECT primary_provider, fallback_providers, output_contract, timeout_ms, kill_switch, canary_percent
            FROM provider_routing_rules
            WHERE tenant_id = :tenant_id AND channel_key = :channel AND scenario = :scenario AND enabled = true
        """), {
            "tenant_id": request.tenant_id,
            "channel": request.channel_key,
            "scenario": request.scenario,
        }).mappings().first()

        if not rule:
            primary_provider = "codex_app_server"
            fallbacks = ["openai_responses", "rule_engine"]
            output_contract = "speedaf_webchat_fast_reply_v1"
            timeout_ms = 10000
            kill_switch = False
            canary_percent = 100
        else:
            primary_provider = rule["primary_provider"]
            fallbacks = _coerce_fallbacks(rule["fallback_providers"])
            output_contract = rule["output_contract"]
            timeout_ms = rule["timeout_ms"]
            kill_switch = rule["kill_switch"]
            canary_percent = rule["canary_percent"] or 0

        primary_provider, fallbacks, output_contract, timeout_ms, kill_switch, canary_percent = _apply_env_overrides(
            primary_provider,
            fallbacks,
            output_contract,
            timeout_ms,
            kill_switch,
            canary_percent,
        )
        primary_provider, fallbacks = _remove_retired_providers(primary_provider, fallbacks)

        if kill_switch:
            self._write_audit(request, "generate", "skipped", primary_provider, 0, {"kill_switch": True}, "kill_switch_active")
            if fallbacks:
                primary_provider = fallbacks[0]
                fallbacks = fallbacks[1:]
            else:
                return ProviderResult.unavailable("router", "kill_switch_active", 0)

        if primary_provider == "codex_app_server" and canary_percent <= 0 and fallbacks:
            primary_provider = fallbacks[0]
            fallbacks = fallbacks[1:]
        elif primary_provider == "codex_app_server" and 0 < canary_percent < 100 and fallbacks:
            bucket = self._stable_percent_bucket(request.tenant_id, request.channel_key, request.session_id)
            if bucket >= canary_percent:
                primary_provider = fallbacks[0]
                fallbacks = fallbacks[1:]

        request.output_contract = output_contract
        request.timeout_ms = timeout_ms
        providers_to_try = [primary_provider] + list(fallbacks)
        seen: set[str] = set()
        providers_to_try = [name for name in providers_to_try if name and not (name in seen or seen.add(name))]
        primary_failure: ProviderResult | None = None

        for provider_name in providers_to_try:
            health_decision = ProviderRuntimeHealth.should_skip(provider_name)
            if health_decision.skip:
                self._write_audit(
                    request,
                    "generate",
                    "skipped",
                    provider_name,
                    0,
                    {"provider_health": health_decision.safe_summary()},
                    health_decision.reason or "provider_health_skip",
                )
                if provider_name == primary_provider:
                    primary_failure = ProviderResult.unavailable(provider_name, health_decision.reason or "provider_health_skip", 0)
                continue

            adapter = ProviderRegistry.get(provider_name, self.db)
            if not adapter:
                self._write_audit(request, "generate", "failed", provider_name, 0, {}, "adapter_not_registered")
                continue

            result = await adapter.generate(self.db, request)
            if not result.ok:
                safe_summary = dict(result.raw_payload_safe_summary or {})
                health_event = ProviderRuntimeHealth.record_failure(provider_name, result.error_code)
                if health_event:
                    safe_summary["provider_health"] = health_event
                    result.raw_payload_safe_summary = safe_summary
                self._write_audit(request, "generate", "failed", provider_name, result.elapsed_ms, safe_summary, result.error_code)
                if provider_name == primary_provider:
                    primary_failure = result
                if not result.fallback_allowed:
                    return result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("No structured output provided")
                if request.scenario == "webchat_fast_reply" and output_contract == "speedaf_webchat_fast_reply_v1":
                    parsed = self._parse_webchat_fast_output(request, result)
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
                self._write_audit(request, "generate", "ok", provider_name, result.elapsed_ms, safe_summary)
                return result
            except Exception as exc:
                safe_summary = {"parse_error": str(exc)[:500]}
                health_event = ProviderRuntimeHealth.record_failure(provider_name, "parse_reject")
                if health_event:
                    safe_summary["provider_health"] = health_event
                self._write_audit(request, "parse_reject", "failed", provider_name, result.elapsed_ms, safe_summary, "parse_reject")
                if provider_name == primary_provider:
                    primary_failure = ProviderResult.unavailable(provider_name, "parse_reject", result.elapsed_ms)
                continue

        if primary_provider == "codex_direct" and primary_failure is not None:
            return primary_failure
        self._write_audit(request, "generate", "failed", "router", 0, {}, "all_providers_failed")
        return ProviderResult.unavailable("router", "all_providers_failed", 0)


def _apply_env_overrides(
    primary_provider: str,
    fallbacks: list[str],
    output_contract: str,
    timeout_ms: int,
    kill_switch: bool,
    canary_percent: int,
) -> tuple[str, list[str], str, int, bool, int]:
    env_primary = os.getenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "").strip()
    if env_primary:
        if env_primary not in _DIRECT_ENV_PROVIDERS:
            raise RuntimeError("PROVIDER_RUNTIME_PRIMARY_PROVIDER must be a registered provider")
        primary_provider = env_primary
        env_fallbacks = os.getenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "")
        if env_fallbacks:
            fallbacks = _coerce_fallbacks(env_fallbacks)
        elif primary_provider == "codex_direct":
            fallbacks = _coerce_fallbacks(os.getenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "openai_responses,rule_engine"))
    env_contract = os.getenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT", "").strip()
    if env_contract:
        output_contract = env_contract
    timeout_ms = _int_env("PROVIDER_RUNTIME_TIMEOUT_MS", timeout_ms, minimum=500, maximum=120000)
    timeout_ms = _harmonize_provider_timeout_ms(primary_provider=primary_provider, timeout_ms=timeout_ms)
    canary_percent = _int_env("PROVIDER_RUNTIME_CANARY_PERCENT", canary_percent, minimum=0, maximum=100)
    if os.getenv("PROVIDER_RUNTIME_KILL_SWITCH") is not None:
        kill_switch = _env_bool("PROVIDER_RUNTIME_KILL_SWITCH", kill_switch)
    return primary_provider, fallbacks, output_contract, timeout_ms, kill_switch, canary_percent


def _remove_retired_providers(primary_provider: str, fallbacks: list[str]) -> tuple[str, list[str]]:
    cleaned_fallbacks = [provider for provider in fallbacks if provider not in _REMOVED_PROVIDERS]
    if primary_provider in _REMOVED_PROVIDERS:
        if cleaned_fallbacks:
            return cleaned_fallbacks[0], cleaned_fallbacks[1:]
        return "rule_engine", []
    return primary_provider, cleaned_fallbacks


def _harmonize_provider_timeout_ms(*, primary_provider: str, timeout_ms: int) -> int:
    """Prevent outer provider_runtime timeout from undercutting provider-owned timeout.

    Codex Direct runs a CLI subprocess and owns its hard timeout through
    CODEX_DIRECT_TIMEOUT_SECONDS. If provider_runtime is lower than that, the
    adapter is forced to kill Codex before its configured budget is reached.
    That caused production 10s timeouts while Codex Direct was configured for
    25s. Keep this harmonization specific to codex_direct; other providers keep
    their existing timeout behavior.
    """

    if primary_provider != "codex_direct":
        return timeout_ms
    if _env_bool("CODEX_DIRECT_ALLOW_OUTER_TIMEOUT_CAP", False):
        return timeout_ms
    codex_timeout_ms = _int_env("CODEX_DIRECT_TIMEOUT_SECONDS", 25, minimum=1, maximum=120) * 1000
    buffer_ms = _int_env("CODEX_DIRECT_TIMEOUT_BUDGET_BUFFER_MS", 1000, minimum=0, maximum=10000)
    return min(120000, max(timeout_ms, codex_timeout_ms + buffer_ms))


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


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))
