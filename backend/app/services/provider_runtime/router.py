import json
import uuid
import hashlib
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone
from .schemas import ProviderRequest, ProviderResult
from .registry import ProviderRegistry
from .output_contracts import OutputContracts
import logging

logger = logging.getLogger(__name__)

class ProviderRuntimeRouter:
    def __init__(self, db: Session):
        self.db = db

    def _write_audit(self, request: ProviderRequest, operation: str, status: str, provider: str, elapsed_ms: int, safe_summary: dict, error_code: str = None):
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
                "safe_summary": json.dumps(safe_summary) if safe_summary else None,
                "error_code": error_code,
                "elapsed_ms": elapsed_ms,
                "now": datetime.now(timezone.utc)
            })
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to write provider audit log: {e}")
            self.db.rollback()

    def _stable_percent_bucket(self, tenant_id: str, session_id: str, request_id: str) -> int:
        raw = f"{tenant_id}:{session_id}:{request_id}"
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
        return int(digest[:8], 16) % 100

    async def route(self, request: ProviderRequest) -> ProviderResult:
        query = text("""
            SELECT primary_provider, fallback_providers, output_contract, timeout_ms, kill_switch, canary_percent
            FROM provider_routing_rules
            WHERE tenant_id = :tenant_id AND channel_key = :channel AND scenario = :scenario AND enabled = true
        """)
        rule = self.db.execute(query, {
            "tenant_id": request.tenant_id,
            "channel": request.channel_key,
            "scenario": request.scenario
        }).mappings().first()

        if not rule:
            primary_provider = "openai_responses"
            fallbacks = ["codex_app_server", "rule_engine"]
            output_contract = "speedaf_webchat_fast_reply_v1"
            timeout_ms = 3000
            kill_switch = False
            canary_percent = 0
        else:
            primary_provider = rule['primary_provider']
            fallbacks = rule['fallback_providers'] or []
            output_contract = rule['output_contract']
            timeout_ms = rule['timeout_ms']
            kill_switch = rule['kill_switch']
            canary_percent = rule['canary_percent']

        if kill_switch:
            self._write_audit(request, "generate", "skipped", "router", 0, {"kill_switch": True}, "kill_switch_active")
            return ProviderResult.unavailable("router", "kill_switch_active", 0)

        # Apply canary routing
        if canary_percent > 0:
            bucket = self._stable_percent_bucket(request.tenant_id, request.session_id, request.request_id)
            if bucket < canary_percent and fallbacks:
                # Canary uses the first fallback provider as its experimental primary
                # In real scenario, a dedicated `canary_provider` column could be added,
                # but we'll prepend the first fallback as primary for the canary.
                primary_provider = fallbacks[0]

        request.output_contract = output_contract
        request.timeout_ms = timeout_ms

        providers_to_try = [primary_provider] + fallbacks
        # Deduplicate while preserving order
        seen = set()
        providers_to_try = [p for p in providers_to_try if not (p in seen or seen.add(p))]

        for p_name in providers_to_try:
            adapter = ProviderRegistry.get(p_name, self.db)
            if not adapter:
                self._write_audit(request, "generate", "failed", p_name, 0, {}, "adapter_not_registered")
                continue

            result = await adapter.generate(self.db, request)
            if result.ok:
                try:
                    if result.structured_output:
                        parsed = OutputContracts.validate_and_parse(
                            output_contract, 
                            json.dumps(result.structured_output),
                            request.tracking_fact_evidence_present
                        )
                        result.structured_output = parsed
                    else:
                        raise ValueError("No structured output provided")
                        
                    self._write_audit(request, "generate", "ok", p_name, result.elapsed_ms, result.raw_payload_safe_summary)
                    return result
                except Exception as e:
                    self._write_audit(request, "parse_reject", "failed", p_name, result.elapsed_ms, {"parse_error": str(e)}, "parse_reject")
                    # Fallback to next provider
                    continue
            
            self._write_audit(request, "generate", "failed", p_name, result.elapsed_ms, result.raw_payload_safe_summary, result.error_code)
            
            if not result.fallback_allowed:
                return result

        self._write_audit(request, "generate", "failed", "router", 0, {}, "all_providers_failed")
        return ProviderResult.unavailable("router", "all_providers_failed", 0)
