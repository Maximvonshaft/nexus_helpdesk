from __future__ import annotations

from .registry import ProviderRegistry
from .schemas import ProviderCapabilities, ProviderRequest, ProviderResult

_BOOTSTRAPPED = False


def bootstrap_provider_runtime() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    from .adapters.capability_verified_private_ai_runtime import (
        CapabilityVerifiedPrivateAIRuntimeAdapter,
    )

    ProviderRegistry.register(
        "private_ai_runtime",
        lambda db: CapabilityVerifiedPrivateAIRuntimeAdapter(),
    )
    _BOOTSTRAPPED = True


# Intentionally no import-time bootstrap side effects. The router calls
# bootstrap_provider_runtime() lazily before resolving adapters.
