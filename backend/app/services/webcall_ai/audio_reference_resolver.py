from __future__ import annotations

from ...voice_models import WebchatVoiceSession
from .config import WebCallAISettings, get_webcall_ai_settings


def resolve_audio_reference_for_session(
    session: WebchatVoiceSession,
    worker_id: str,
    settings: WebCallAISettings | None = None,
) -> str | None:
    resolved = settings or get_webcall_ai_settings()
    resolved.validate_runtime()

    if resolved.audio_reference_source == "disabled":
        return None
    if resolved.audio_reference_source == "static_fixture":
        if not resolved.audio_reference_static_url:
            raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL is required for static_fixture")
        return resolved.audio_reference_static_url

    raise RuntimeError("WEBCALL_AI_AUDIO_REFERENCE_SOURCE must be disabled or static_fixture in PR-7")
