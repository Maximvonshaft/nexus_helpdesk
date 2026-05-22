from __future__ import annotations

import hashlib
import json
import logging
from ..webchat_fast_config import WebchatFastSettings
from ..webchat_fast_reply_metrics import record_codex_app_server_metric
from .codex_app_server_provider import CodexAppServerProvider
from .codex_auth_provider import CodexAuthProvider
from .openai_responses_provider import OpenAIResponsesProvider
from .openclaw_responses_provider import OpenClawResponsesProvider
from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult
from app.db import SessionLocal
from ..provider_runtime.schemas import ProviderRequest
from ..provider_runtime.router import ProviderRuntimeRouter

logger = logging.getLogger(__name__)

def _provider_for(name: str, settings: WebchatFastSettings) -> BaseFastAIProvider:
    if name == "openclaw_responses":
        return OpenClawResponsesProvider(settings)
    if name == "codex_auth":
        return CodexAuthProvider(settings)
    if name == "codex_app_server":
        return CodexAppServerProvider(settings)
    if name == "openai_responses":
        return OpenAIResponsesProvider(settings)
    raise ValueError(f"Unsupported WEBCHAT_FAST_AI_PROVIDER: {name}")

def _stable_percent_bucket(*, tenant_key: str, session_id: str, request_id: str | None) -> int:
    raw = f"{tenant_key or 'default'}:{session_id or ''}:{request_id or ''}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % 100

def _effective_provider_name(*, request: FastAIProviderRequest, settings: WebchatFastSettings) -> tuple[str, str]:
    if settings.provider != "codex_app_server":
        return settings.provider, "configured_provider"
    if settings.codex_app_server_kill_switch:
        return "openclaw_responses", "kill_switch_openclaw"
    percent = settings.codex_app_server_canary_percent
    if percent >= 100:
        return "codex_app_server", "canary_full"
    bucket = _stable_percent_bucket(
        tenant_key=request.tenant_key,
        session_id=request.session_id,
        request_id=request.request_id,
    )
    if bucket < percent:
        return "codex_app_server", "canary_selected"
    return "openclaw_responses", "canary_skipped_openclaw"

async def generate_fast_reply(
    *,
    request: FastAIProviderRequest,
    settings: WebchatFastSettings,
) -> FastAIProviderResult:
    if settings.provider == "provider_runtime":
        db = SessionLocal()
        try:
            router = ProviderRuntimeRouter(db)
            pr_req = ProviderRequest(
                request_id=request.request_id or "req_unknown",
                tenant_id=request.tenant_key,
                tenant_key=request.tenant_key,
                channel_key=request.channel_key,
                session_id=request.session_id,
                scenario="webchat_fast_reply",
                body=request.body,
                recent_context=request.recent_context,
                tracking_fact_summary=request.tracking_fact_summary,
                tracking_fact_evidence_present=request.tracking_fact_evidence_present,
                output_contract="speedaf_webchat_fast_reply_v1",
                timeout_ms=10000,
                metadata={}
            )
            res = await router.route(pr_req)
            if not res.ok or not res.structured_output:
                return FastAIProviderResult.unavailable("provider_runtime", res.error_code or "all_failed", res.elapsed_ms)
            
            output = res.structured_output
            safe_summary = res.raw_payload_safe_summary or {}
            safe_summary["provider_runtime"] = True
            reply = output.get("customer_reply") or output.get("reply")
            
            return FastAIProviderResult(
                ok=True,
                ai_generated=True,
                reply_source=res.provider,
                raw_provider=res.provider,
                raw_payload_safe_summary=safe_summary,
                reply=reply,
                intent=output.get("intent"),
                tracking_number=output.get("tracking_number"),
                handoff_required=output.get("handoff_required", False),
                handoff_reason=output.get("handoff_reason"),
                recommended_agent_action=output.get("recommended_agent_action"),
                tool_intents=[],
                elapsed_ms=res.elapsed_ms,
            )
        except Exception as e:
            logger.exception("ProviderRuntimeRouter failed")
            return FastAIProviderResult.unavailable("provider_runtime", "router_exception", 0)
        finally:
            db.close()
            
    # Legacy flow
    primary_name, route = _effective_provider_name(request=request, settings=settings)
    if settings.provider == "codex_app_server":
        record_codex_app_server_metric(status="route", route=route)
    primary = _provider_for(primary_name, settings)
    result = await primary.generate(request)
    if result.ok:
        if primary_name == "codex_app_server":
            record_codex_app_server_metric(status="ok", route=route, elapsed_ms=result.elapsed_ms)
        return result

    fallback_name = settings.fallback_provider
    if fallback_name == "none" or fallback_name == primary_name:
        if primary_name == "codex_app_server":
            record_codex_app_server_metric(
                status="error",
                route=route,
                elapsed_ms=result.elapsed_ms,
                error_code=result.error_code,
            )
        return result

    fallback = _provider_for(fallback_name, settings)
    fallback_result = await fallback.generate(request)
    if fallback_result.ok:
        if primary_name == "codex_app_server":
            record_codex_app_server_metric(
                status="fallback_ok",
                route=route,
                elapsed_ms=result.elapsed_ms,
                error_code=result.error_code,
            )
        return fallback_result
    if primary_name == "codex_app_server":
        record_codex_app_server_metric(
            status="fallback_failed",
            route=route,
            elapsed_ms=result.elapsed_ms,
            error_code=result.error_code,
        )
    return result
