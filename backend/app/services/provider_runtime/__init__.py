from __future__ import annotations

from .registry import ProviderRegistry
from .schemas import ProviderCapabilities, ProviderRequest, ProviderResult

_BOOTSTRAPPED = False


def bootstrap_provider_runtime() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    from .adapters.private_ai_runtime import PrivateAIRuntimeAdapter

    ProviderRegistry.register("private_ai_runtime", lambda db: PrivateAIRuntimeAdapter())

    _BOOTSTRAPPED = True


# Intentionally no import-time bootstrap side effects. The router calls
# bootstrap_provider_runtime() lazily before resolving adapters.
