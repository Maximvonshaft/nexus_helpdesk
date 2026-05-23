from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceSession
from .audio_reference_resolver import resolve_audio_reference_for_session
from .config import WebCallAISettings, get_webcall_ai_settings
from .media_schemas import WebCallSTTInput, WebCallSTTResult
from .provider_router import get_stt_provider
from .transcript_writer import write_stt_transcript_segment


@dataclass(frozen=True)
class WebCallSTTRuntimeResult:
    usable: bool
    provider: str
    text_redacted: str | None
    language: str | None
    confidence: int | None
    transcript_segment_id: int | None
    stt_events: int
    status: str
    error_code: str | None = None


def run_stt_runtime_for_session(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    participant_identity: str,
    settings: WebCallAISettings | None = None,
) -> WebCallSTTRuntimeResult:
    resolved = settings or get_webcall_ai_settings()
    audio_reference = None
    if resolved.stt_runtime_mode == "audio_reference":
        audio_reference = resolve_audio_reference_for_session(session, worker_id, resolved)
        if not audio_reference:
            return _unusable("stt_runtime", "stt_audio_reference_required")
    elif resolved.stt_runtime_mode != "mock_text":
        return _unusable("stt_runtime", "stt_runtime_mode_invalid")

    try:
        stt_result = get_stt_provider(resolved).transcribe(
            WebCallSTTInput(
                voice_session_id=session.id,
                worker_id=worker_id,
                locale=session.ai_language,
                audio_reference=audio_reference,
            )
        )
    except Exception:
        return _unusable("stt_runtime", "stt_provider_exception")

    if not _usable_stt_result(stt_result):
        return WebCallSTTRuntimeResult(
            usable=False,
            provider=stt_result.provider,
            text_redacted=None,
            language=stt_result.language,
            confidence=None,
            transcript_segment_id=None,
            stt_events=stt_result.event_count,
            status=stt_result.status,
            error_code=stt_result.error_code or "stt_result_unusable",
        )

    transcript_segment_id = None
    if resolved.stt_transcript_write_enabled:
        transcript_result = write_stt_transcript_segment(
            db,
            session=session,
            stt_result=stt_result,
            participant_identity=participant_identity,
            settings=resolved,
        )
        transcript_segment_id = transcript_result.segment.id

    return WebCallSTTRuntimeResult(
        usable=True,
        provider=stt_result.provider,
        text_redacted=stt_result.text_redacted,
        language=stt_result.language,
        confidence=stt_result.confidence,
        transcript_segment_id=transcript_segment_id,
        stt_events=stt_result.event_count,
        status="ok",
    )


def _usable_stt_result(stt_result: WebCallSTTResult) -> bool:
    return (
        stt_result.status == "ok"
        and stt_result.is_final
        and bool(stt_result.text_redacted)
        and bool(stt_result.language)
        and stt_result.confidence is not None
    )


def _unusable(provider: str, error_code: str) -> WebCallSTTRuntimeResult:
    return WebCallSTTRuntimeResult(
        usable=False,
        provider=provider,
        text_redacted=None,
        language=None,
        confidence=None,
        transcript_segment_id=None,
        stt_events=0,
        status="unavailable",
        error_code=error_code,
    )
