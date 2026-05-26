from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .output_contracts import OutputContracts
from .registry import ProviderRegistry
from .schemas import ProviderRequest, ProviderResult

logger = logging.getLogger(__name__)


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
            fallbacks = ["openclaw_responses", "rule_engine"]
            output_contract = "speedaf_webchat_fast_reply_v1"
            timeout_ms = 10000
            kill_switch = False
            canary_percent = 0
        else:
            primary_provider = rule["primary_provider"]
            fallbacks = _coerce_fallbacks(rule["fallback_providers"])
            output_contract = rule["output_contract"]
            timeout_ms = rule["timeout_ms"]
            kill_switch = rule["kill_switch"]
            canary_percent = rule["canary_percent"] or 0

        if kill_switch and primary_provider == "codex_app_server":
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

        for provider_name in providers_to_try:
            adapter = ProviderRegistry.get(provider_name, self.db)
            if not adapter:
                self._write_audit(request, "generate", "failed", provider_name, 0, {}, "adapter_not_registered")
                continue

            result = await adapter.generate(self.db, request)
            if not result.ok:
                self._write_audit(request, "generate", "failed", provider_name, result.elapsed_ms, result.raw_payload_safe_summary, result.error_code)
                if not result.fallback_allowed:
                    return result
                continue

            try:
                if not result.structured_output:
                    raise ValueError("No structured output provided")
                parsed = OutputContracts.validate_and_parse(
                    output_contract,
                    json.dumps(result.structured_output),
                    request.tracking_fact_evidence_present,
                    (request.metadata or {}).get("persona_context"),
                    request.body,
                    (request.metadata or {}).get("knowledge_context"),
                )
                result.structured_output = parsed
                self._write_audit(request, "generate", "ok", provider_name, result.elapsed_ms, result.raw_payload_safe_summary)
                return result
            except Exception as exc:
                self._write_audit(request, "parse_reject", "failed", provider_name, result.elapsed_ms, {"parse_error": str(exc)[:500]}, "parse_reject")
                continue

        self._write_audit(request, "generate", "failed", "router", 0, {}, "all_providers_failed")
        return ProviderResult.unavailable("router", "all_providers_failed", 0)


def _coerce_fallbacks(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    return []
