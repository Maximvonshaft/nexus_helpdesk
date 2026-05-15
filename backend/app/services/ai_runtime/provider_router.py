from __future__ import annotations

from ..webchat_fast_config import WebchatFastSettings
from .codex_auth_provider import CodexAuthProvider
from .openai_responses_provider import OpenAIResponsesProvider
from .openclaw_responses_provider import OpenClawResponsesProvider
from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult


def _provider_for(name: str, settings: WebchatFastSettings) -> BaseFastAIProvider:
    if name == "openclaw_responses":
        return OpenClawResponsesProvider(settings)
    if name == "codex_auth":
        return CodexAuthProvider(settings)
    if name == "openai_responses":
        return OpenAIResponsesProvider(settings)
    raise ValueError(f"Unsupported WEBCHAT_FAST_AI_PROVIDER: {name}")


async def generate_fast_reply(
    *,
    request: FastAIProviderRequest,
    settings: WebchatFastSettings,
) -> FastAIProviderResult:
    primary = _provider_for(settings.provider, settings)
    result = await primary.generate(request)
    if result.ok:
        return result

    fallback_name = settings.fallback_provider
    if fallback_name == "none" or fallback_name == settings.provider:
        return result

    fallback = _provider_for(fallback_name, settings)
    fallback_result = await fallback.generate(request)
    if fallback_result.ok:
        return fallback_result
    return result
