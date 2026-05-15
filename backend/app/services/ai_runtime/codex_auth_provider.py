from __future__ import annotations

import time

from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult


class CodexAuthProvider(BaseFastAIProvider):
    """Phase 1 Codex-auth provider skeleton.

    This intentionally does not assume that a Codex/ChatGPT access token can be
    used as a normal OpenAI API key. Until a real transport is confirmed by the
    Phase 0 probe, the provider only reports a safe, explicit not-confirmed
    result.
    """

    name = "codex_auth"

    def is_configured(self) -> bool:
        return bool(self.settings.codex_enabled and self.settings.codex_token)

    async def generate(self, request: FastAIProviderRequest) -> FastAIProviderResult:
        started = time.monotonic()
        if not self.settings.codex_enabled:
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="codex_auth_disabled",
                elapsed_ms=0,
            )
        if not self.settings.codex_token:
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="codex_auth_not_configured",
                elapsed_ms=0,
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return FastAIProviderResult.unavailable(
            provider=self.name,
            error_code="codex_transport_not_confirmed",
            elapsed_ms=elapsed_ms,
            safe_summary={"transport": "not_confirmed", "token_present": True},
        )
