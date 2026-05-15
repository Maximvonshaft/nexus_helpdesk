from __future__ import annotations

import time

from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult


class OpenAIResponsesProvider(BaseFastAIProvider):
    """Phase 1 OpenAI Responses provider skeleton.

    The real OpenAI API implementation is intentionally deferred. This keeps the
    branch focused on provider routing and preserves current production behavior.
    """

    name = "openai_responses"

    def is_configured(self) -> bool:
        return bool(self.settings.openai_enabled and self.settings.openai_token)

    async def generate(self, request: FastAIProviderRequest) -> FastAIProviderResult:
        started = time.monotonic()
        if not self.settings.openai_enabled:
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="openai_responses_disabled",
                elapsed_ms=0,
            )
        if not self.settings.openai_token:
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="openai_responses_not_configured",
                elapsed_ms=0,
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return FastAIProviderResult.unavailable(
            provider=self.name,
            error_code="openai_responses_not_implemented",
            elapsed_ms=elapsed_ms,
            safe_summary={"transport": "not_implemented"},
        )
