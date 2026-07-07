from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


@dataclass(frozen=True)
class WebchatRuntimeSettings:
    enabled: bool
    history_turns: int
    app_env: str


@lru_cache(maxsize=1)
def get_webchat_runtime_settings() -> WebchatRuntimeSettings:
    return WebchatRuntimeSettings(
        enabled=_env_bool("WEBCHAT_AI_ENABLED", True),
        history_turns=_env_int("WEBCHAT_AI_HISTORY_TURNS", 5, minimum=1, maximum=5),
        app_env=os.getenv("APP_ENV", "development").strip().lower() or "development",
    )
