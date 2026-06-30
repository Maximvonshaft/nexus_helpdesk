from __future__ import annotations

import os

from .credential_crypto import CredentialCryptoService
from .registry import ProviderAdapter, ProviderRegistry
from .schemas import ProviderCapabilities, ProviderRequest, ProviderResult

_BOOTSTRAPPED = False


def bootstrap_provider_runtime() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    from .adapters.codex_app_server import CodexAppServerAdapter
    from .adapters import codex_direct_prompt_budget_patch as _codex_direct_prompt_budget_patch  # noqa: F401
    from .adapters.codex_direct import CodexDirectAdapter
    from .adapters.openai_responses import OpenAIResponsesAdapter
    from .adapters.private_ai_runtime import PrivateAIRuntimeAdapter

    def codex_factory(db):
        bridge_url = os.environ.get("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
        return CodexAppServerAdapter(CredentialCryptoService(), bridge_url)

    def openai_factory(db):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        return OpenAIResponsesAdapter(api_key)

    ProviderRegistry.register("codex_app_server", codex_factory)
    ProviderRegistry.register("codex_direct", lambda db: CodexDirectAdapter())
    ProviderRegistry.register("openai_responses", openai_factory)
    ProviderRegistry.register("private_ai_runtime", lambda db: PrivateAIRuntimeAdapter())

    class SkeletonAdapter(ProviderAdapter):
        capabilities = ProviderCapabilities()

        def __init__(self, name: str):
            self.name = name

        async def generate(self, db, req: ProviderRequest) -> ProviderResult:
            return ProviderResult.unavailable(self.name, f"{self.name}_skeleton_unavailable", 0)

    ProviderRegistry.register("anthropic", lambda db: SkeletonAdapter("anthropic"))
    ProviderRegistry.register("gemini", lambda db: SkeletonAdapter("gemini"))
    ProviderRegistry.register("openrouter", lambda db: SkeletonAdapter("openrouter"))
    ProviderRegistry.register("rule_engine", lambda db: SkeletonAdapter("rule_engine"))
    _BOOTSTRAPPED = True


# Intentionally no import-time bootstrap side effects. The router calls
# bootstrap_provider_runtime() lazily before resolving adapters.
