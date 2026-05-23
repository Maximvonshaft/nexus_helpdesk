from __future__ import annotations

from dataclasses import dataclass

from ...voice_models import WebchatVoiceSession, WebchatVoiceAITurn
from .config import WebCallAISettings, get_webcall_ai_settings
from .media_schemas import WebCallTTSInput, WebCallTTSResult
from .provider_router import get_tts_provider


@dataclass(frozen=True)
class WebCallTTSRuntimeResult:
    usable: bool
    provider: str
    voice: str
    language: str
    text_redacted: str
    audio_reference: str | None
    tts_events: int
    status: str
    error_code: str | None = None


def run_tts_runtime_for_turn(
    *,
    turn: WebchatVoiceAITurn,
    session: WebchatVoiceSession,
    worker_id: str,
    settings: WebCallAISettings | None = None,
) -> WebCallTTSRuntimeResult:
    resolved = settings or get_webcall_ai_settings()
    text_redacted = (turn.ai_response_text_redacted or "").strip()
    language = (turn.language or session.ai_language or "en").strip() or "en"
    if not text_redacted:
        return _unusable(provider="tts_runtime", language=language, text_redacted="", error_code="tts_reply_text_required")
    try:
        tts_result = get_tts_provider(resolved).synthesize(
            WebCallTTSInput(
                voice_session_id=session.id,
                worker_id=worker_id,
                text_redacted=text_redacted,
                language=language,
            )
        )
    except Exception:
        return _unusable(
            provider="tts_runtime",
            language=language,
            text_redacted=text_redacted,
            error_code="tts_provider_exception",
        )
    if not _usable_tts_result(tts_result):
        return WebCallTTSRuntimeResult(
            usable=False,
            provider=tts_result.provider,
            voice=tts_result.voice,
            language=tts_result.language,
            text_redacted=tts_result.text_redacted,
            audio_reference=None,
            tts_events=tts_result.event_count,
            status=tts_result.synthesis_status,
            error_code=tts_result.error_code or "tts_result_unusable",
        )
    return WebCallTTSRuntimeResult(
        usable=True,
        provider=tts_result.provider,
        voice=tts_result.voice,
        language=tts_result.language,
        text_redacted=tts_result.text_redacted,
        audio_reference=tts_result.audio_reference,
        tts_events=tts_result.event_count,
        status=tts_result.synthesis_status,
    )


def _usable_tts_result(tts_result: WebCallTTSResult) -> bool:
    return (
        tts_result.synthesis_status in {"mock_synthesized", "ok"}
        and bool(tts_result.audio_reference)
        and bool(tts_result.text_redacted)
    )


def _unusable(*, provider: str, language: str, text_redacted: str, error_code: str) -> WebCallTTSRuntimeResult:
    return WebCallTTSRuntimeResult(
        usable=False,
        provider=provider,
        voice="mock_support_voice",
        language=language,
        text_redacted=text_redacted,
        audio_reference=None,
        tts_events=0,
        status="unavailable",
        error_code=error_code,
    )
