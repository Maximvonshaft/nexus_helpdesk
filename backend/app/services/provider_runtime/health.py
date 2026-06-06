from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

FAILOVER_WORTHY_ERROR_CODES = {
    "codex_direct_timeout",
    "codex_direct_nonzero_exit",
    "codex_direct_empty_reply",
    "codex_direct_bad_json",
    "parse_reject",
    "openai_responses_timeout",
    "openai_responses_http_429",
    "openai_responses_http_500",
    "openai_responses_http_502",
    "openai_responses_http_503",
    "openai_responses_http_504",
}


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
    """Small in-process provider health state for fail-fast routing.

    This is intentionally in-memory for the first production slice: it avoids new
    migrations and makes rollback trivial. Multi-process deployments will still
    gain protection per worker process; a later PR can persist this to Redis or a
    database table if the data proves it is needed.
    """

    _failure_events: dict[str, list[datetime]] = {}
    _cooldown_until: dict[str, datetime] = {}

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._failure_events.clear()
        cls._cooldown_until.clear()

    @classmethod
    def enabled(cls) -> bool:
        return _env_bool("PROVIDER_RUNTIME_HEALTH_FALLBACK_ENABLED", True)

    @classmethod
    def failure_threshold(cls) -> int:
        return _int_env("PROVIDER_RUNTIME_HEALTH_FAILURE_THRESHOLD", 2, minimum=1, maximum=20)

    @classmethod
    def failure_window_seconds(cls) -> int:
        return _int_env("PROVIDER_RUNTIME_HEALTH_FAILURE_WINDOW_SECONDS", 300, minimum=10, maximum=3600)

    @classmethod
    def cooldown_seconds(cls) -> int:
        return _int_env("PROVIDER_RUNTIME_HEALTH_COOLDOWN_SECONDS", 180, minimum=10, maximum=3600)

    @classmethod
    def should_skip(cls, provider: str) -> ProviderHealthDecision:
        if not cls.enabled():
            return ProviderHealthDecision(skip=False)
        now = datetime.now(timezone.utc)
        cooldown_until = cls._cooldown_until.get(provider)
        failures = cls._recent_failures(provider, now)
        if cooldown_until and cooldown_until > now:
            return ProviderHealthDecision(
                skip=True,
                reason="provider_in_cooldown",
                cooldown_until=cooldown_until.isoformat(),
                consecutive_failures=len(failures),
            )
        if cooldown_until and cooldown_until <= now:
            cls._cooldown_until.pop(provider, None)
        return ProviderHealthDecision(skip=False, consecutive_failures=len(failures))

    @classmethod
    def record_success(cls, provider: str) -> dict[str, Any] | None:
        if not cls.enabled():
            return None
        had_state = bool(cls._failure_events.get(provider) or cls._cooldown_until.get(provider))
        cls._failure_events.pop(provider, None)
        cls._cooldown_until.pop(provider, None)
        if not had_state:
            return None
        return {
            "health_event": "provider_recovered",
            "provider": provider,
            "cooldown_cleared": True,
        }

    @classmethod
    def record_failure(cls, provider: str, error_code: str | None) -> dict[str, Any] | None:
        if not cls.enabled() or not is_failover_worthy_provider_error(error_code):
            return None
        now = datetime.now(timezone.utc)
        failures = cls._recent_failures(provider, now)
        failures.append(now)
        cls._failure_events[provider] = failures
        threshold = cls.failure_threshold()
        summary: dict[str, Any] = {
            "health_event": "provider_failure_recorded",
            "provider": provider,
            "error_code": error_code,
            "consecutive_failures": len(failures),
            "failure_threshold": threshold,
        }
        if len(failures) >= threshold:
            cooldown_until = now + timedelta(seconds=cls.cooldown_seconds())
            cls._cooldown_until[provider] = cooldown_until
            summary.update(
                {
                    "health_event": "provider_cooldown_set",
                    "cooldown_until": cooldown_until.isoformat(),
                    "cooldown_seconds": cls.cooldown_seconds(),
                }
            )
        return summary

    @classmethod
    def _recent_failures(cls, provider: str, now: datetime) -> list[datetime]:
        cutoff = now - timedelta(seconds=cls.failure_window_seconds())
        failures = [event for event in cls._failure_events.get(provider, []) if event >= cutoff]
        cls._failure_events[provider] = failures
        return failures


def is_failover_worthy_provider_error(error_code: str | None) -> bool:
    return bool(error_code and error_code in FAILOVER_WORTHY_ERROR_CODES)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))
