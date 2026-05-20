import json
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from .schemas import ProviderRequest, ProviderResult
from .registry import ProviderRegistry
from .output_contracts import OutputContracts

class ProviderRuntimeRouter:
    def __init__(self, db: Session):
        self.db = db

    async def route(self, request: ProviderRequest) -> ProviderResult:
        query = text("""
            SELECT primary_provider, fallback_providers, output_contract, timeout_ms, kill_switch
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
        else:
            primary_provider = rule['primary_provider']
            fallbacks = rule['fallback_providers'] or []
            output_contract = rule['output_contract']
            timeout_ms = rule['timeout_ms']
            kill_switch = rule['kill_switch']

        if kill_switch:
            return ProviderResult.unavailable("router", "kill_switch_active", 0)

        request.output_contract = output_contract
        request.timeout_ms = timeout_ms

        providers_to_try = [primary_provider] + fallbacks

        for p_name in providers_to_try:
            adapter = ProviderRegistry.get(p_name, self.db)
            if not adapter:
                continue

            result = await adapter.generate(self.db, request)
            if result.ok:
                try:
                    if result.structured_output:
                        parsed = OutputContracts.validate_and_parse(output_contract, json.dumps(result.structured_output))
                    else:
                        raise ValueError("No structured output provided")
                    return result
                except Exception as e:
                    continue
            
            if not result.fallback_allowed:
                return result

        return ProviderResult.unavailable("router", "all_providers_failed", 0)
