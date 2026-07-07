from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderHealthDecision:
    skip: bool
    reason: str | None = None
    cooldown_until: str | None = None
    consecutive_failures: int = 0

    def safe_summary(self) -> dict[str, Any]:
        return {
            "health_skip": self.skip,
            "health_skip_reason": self.reason,
            "cooldown_until": self.cooldown_until,
            "consecutive_failures": self.consecutive_failures,
        }


class ProviderRuntimeHealth:
    """Single-runtime health shim.

    Nexus currently has exactly one customer-visible reply provider:
    private_ai_runtime. With no secondary provider, fail-fast cooldown would make
    the channel silent after transient errors, so health never skips the runtime.
    """

    @classmethod
    def reset_for_tests(cls) -> None:
        return None

    @classmethod
    def should_skip(cls, provider: str) -> ProviderHealthDecision:
        return ProviderHealthDecision(skip=False)

    @classmethod
    def record_success(cls, provider: str) -> dict[str, Any] | None:
        return None

    @classmethod
    def record_failure(cls, provider: str, error_code: str | None) -> dict[str, Any] | None:
        return None


def is_failover_worthy_provider_error(error_code: str | None) -> bool:
    return False
