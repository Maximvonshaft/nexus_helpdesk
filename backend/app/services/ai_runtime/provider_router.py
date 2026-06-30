from __future__ import annotations

import hashlib
from ..webchat_fast_config import WebchatFastSettings
from ..webchat_fast_reply_metrics import record_codex_app_server_metric
from .codex_app_server_provider import CodexAppServerProvider
from .codex_auth_provider import CodexAuthProvider
from .openai_responses_provider import OpenAIResponsesProvider
from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult

# Deprecated legacy direct WebChat Fast Reply router. Production provider_runtime
# dispatch lives in app.services.provider_runtime.webchat_fast_dispatcher.


class RuleEngineUnavailableProvider(BaseFastAIProvider):
    name = "rule_engine"

    def is_configured(self) -> bool:
        return False

    async def generate(self, request: FastAIProviderRequest) -> FastAIProviderResult:
        return FastAIProviderResult.unavailable(provider=self.name, error_code="rule_engine_unavailable", elapsed_ms=0)


def _provider_for(name: str, settings: WebchatFastSettings) -> BaseFastAIProvider:
    if name == "codex_auth":
        return CodexAuthProvider(settings)
    if name == "codex_app_server":
        return CodexAppServerProvider(settings)
    if name == "openai_responses":
        return OpenAIResponsesProvider(settings)
    if name == "rule_engine":
        return RuleEngineUnavailableProvider(settings)
    raise ValueError(f"Unsupported WEBCHAT_FAST_AI_PROVIDER: {name}")

def _stable_percent_bucket(*, tenant_key: str, session_id: str, request_id: str | None) -> int:
    raw = f"{tenant_key or 'default'}:{session_id or ''}:{request_id or ''}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % 100

def _effective_provider_name(*, request: FastAIProviderRequest, settings: WebchatFastSettings) -> tuple[str, str]:
    if settings.provider != "codex_app_server":
        return settings.provider, "configured_provider"
    fallback_provider = settings.fallback_provider if settings.fallback_provider != "none" else "rule_engine"
    if settings.codex_app_server_kill_switch:
        return fallback_provider, "kill_switch_fallback"
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
    return fallback_provider, "canary_skipped_fallback"

async def generate_fast_reply(
    *,
    request: FastAIProviderRequest,
    settings: WebchatFastSettings,
) -> FastAIProviderResult:
    if settings.provider == "provider_runtime":
        return FastAIProviderResult.unavailable(
            provider="provider_runtime",
            error_code="provider_runtime_dispatcher_required",
            elapsed_ms=0,
        )

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
