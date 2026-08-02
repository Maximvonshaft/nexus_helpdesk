from __future__ import annotations

import os
from dataclasses import dataclass

from ..settings import Settings, get_settings


@dataclass(frozen=True)
class WebchatRateLimitPolicy:
    window_seconds: int
    max_requests: int


def load_webchat_preauth_rate_limit_policy(
    business_settings: Settings | None = None,
) -> WebchatRateLimitPolicy:
    settings = business_settings or get_settings()
    window_seconds = _bounded_int(
        "WEBCHAT_PREAUTH_RATE_LIMIT_WINDOW_SECONDS",
        settings.webchat_rate_limit_window_seconds,
        1,
        3600,
    )
    max_requests = _bounded_int(
        "WEBCHAT_PREAUTH_RATE_LIMIT_MAX_REQUESTS",
        max(
            3000,
            window_seconds * 50,
            settings.webchat_rate_limit_max_requests * 10,
        ),
        1,
        1_000_000,
    )
    if (
        max_requests * settings.webchat_rate_limit_window_seconds
        < settings.webchat_rate_limit_max_requests * window_seconds
    ):
        raise RuntimeError(
            "WEBCHAT_PREAUTH_RATE_LIMIT_MAX_REQUESTS and "
            "WEBCHAT_PREAUTH_RATE_LIMIT_WINDOW_SECONDS must not create a "
            "stricter request rate than the authorized WebChat limit"
        )
    return WebchatRateLimitPolicy(
        window_seconds=window_seconds,
        max_requests=max_requests,
    )


def _bounded_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


__all__ = [
    "WebchatRateLimitPolicy",
    "load_webchat_preauth_rate_limit_policy",
]
