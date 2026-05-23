from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import WebCallAISettings, get_webcall_ai_settings


@dataclass(frozen=True)
class WebCallAIRealMediaSmokeResult:
    ok: bool
    error_code: str | None = None


class RealMediaSmokePublisher(Protocol):
    def publish_silent_frame(self) -> bool:
        ...


def run_webcall_ai_real_media_smoke(
    *,
    settings: WebCallAISettings | None = None,
    publisher: RealMediaSmokePublisher | None = None,
) -> WebCallAIRealMediaSmokeResult:
    resolved = settings or get_webcall_ai_settings()
    if resolved.app_env == "production":
        return WebCallAIRealMediaSmokeResult(False, "production_rejected")
    if not resolved.pilot_real_media_enabled:
        return WebCallAIRealMediaSmokeResult(False, "livekit_real_media_smoke_unavailable")
    if publisher is None:
        return WebCallAIRealMediaSmokeResult(False, "livekit_real_media_smoke_unavailable")
    try:
        return WebCallAIRealMediaSmokeResult(bool(publisher.publish_silent_frame()), None)
    except Exception:
        return WebCallAIRealMediaSmokeResult(False, "livekit_real_media_smoke_failed")
