from __future__ import annotations

import os
from dataclasses import dataclass

_ALLOWED_MODES = {"simulated_full_loop"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


@dataclass(frozen=True)
class WebCallAIDemoLabSettings:
    demo_lab_enabled: bool
    demo_lab_kill_switch: bool
    demo_lab_mode: str
    demo_lab_allow_browser_speech: bool
    demo_lab_allow_real_media: bool
    demo_lab_tenant_allowlist: tuple[str, ...]
    demo_lab_max_active_sessions: int
    demo_lab_max_turns_per_session: int
    demo_lab_max_input_chars: int
    demo_lab_event_retention_limit: int

    def validate(self) -> None:
        if self.demo_lab_mode not in _ALLOWED_MODES:
            raise RuntimeError("WEBCALL_AI_DEMO_LAB_MODE must be simulated_full_loop")
        if self.demo_lab_max_active_sessions < 1 or self.demo_lab_max_active_sessions > 20:
            raise RuntimeError("WEBCALL_AI_DEMO_LAB_MAX_ACTIVE_SESSIONS must be between 1 and 20")
        if self.demo_lab_max_turns_per_session < 1 or self.demo_lab_max_turns_per_session > 20:
            raise RuntimeError("WEBCALL_AI_DEMO_LAB_MAX_TURNS_PER_SESSION must be between 1 and 20")
        if self.demo_lab_max_input_chars < 1 or self.demo_lab_max_input_chars > 4000:
            raise RuntimeError("WEBCALL_AI_DEMO_LAB_MAX_INPUT_CHARS must be between 1 and 4000")
        if self.demo_lab_event_retention_limit < 10 or self.demo_lab_event_retention_limit > 1000:
            raise RuntimeError("WEBCALL_AI_DEMO_LAB_EVENT_RETENTION_LIMIT must be between 10 and 1000")
        if self.demo_lab_allow_real_media:
            raise RuntimeError("WEBCALL_AI_DEMO_LAB_ALLOW_REAL_MEDIA must remain false in this demo sandbox PR")


def get_webcall_ai_demo_lab_settings() -> WebCallAIDemoLabSettings:
    settings = WebCallAIDemoLabSettings(
        demo_lab_enabled=_env_bool("WEBCALL_AI_DEMO_LAB_ENABLED", False),
        demo_lab_kill_switch=_env_bool("WEBCALL_AI_DEMO_LAB_KILL_SWITCH", True),
        demo_lab_mode=os.getenv("WEBCALL_AI_DEMO_LAB_MODE", "simulated_full_loop").strip().lower()
        or "simulated_full_loop",
        demo_lab_allow_browser_speech=_env_bool("WEBCALL_AI_DEMO_LAB_ALLOW_BROWSER_SPEECH", True),
        demo_lab_allow_real_media=_env_bool("WEBCALL_AI_DEMO_LAB_ALLOW_REAL_MEDIA", False),
        demo_lab_tenant_allowlist=_csv(os.getenv("WEBCALL_AI_DEMO_LAB_TENANT_ALLOWLIST")),
        demo_lab_max_active_sessions=_env_int("WEBCALL_AI_DEMO_LAB_MAX_ACTIVE_SESSIONS", 3),
        demo_lab_max_turns_per_session=_env_int("WEBCALL_AI_DEMO_LAB_MAX_TURNS_PER_SESSION", 8),
        demo_lab_max_input_chars=_env_int("WEBCALL_AI_DEMO_LAB_MAX_INPUT_CHARS", 1000),
        demo_lab_event_retention_limit=_env_int("WEBCALL_AI_DEMO_LAB_EVENT_RETENTION_LIMIT", 200),
    )
    settings.validate()
    return settings
