from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceSession, WebchatVoiceTranscriptSegment
from .config import WebCallAISettings, get_webcall_ai_settings
from .media_schemas import WebCallSTTResult

CUSTOMER_PARTICIPANT_IDENTITY = "visitor"
CUSTOMER_SPEAKER_LABEL = "Customer"
CUSTOMER_SPEAKER_TYPE = "visitor"


@dataclass(frozen=True)
class TranscriptWriteResult:
    segment: WebchatVoiceTranscriptSegment
    created: bool


def write_stt_transcript_segment(
    db: Session,
    *,
    session: WebchatVoiceSession,
    stt_result: WebCallSTTResult,
    participant_identity: str = CUSTOMER_PARTICIPANT_IDENTITY,
    segment_index: int | None = None,
    settings: WebCallAISettings | None = None,
) -> TranscriptWriteResult:
    resolved = settings or get_webcall_ai_settings()
    provider_session_id = _provider_session_id(session, resolved)
    segment_id = _segment_id(session, segment_index)
    provider = stt_result.provider or "mock"
    segment = (
        db.query(WebchatVoiceTranscriptSegment)
        .filter(
            WebchatVoiceTranscriptSegment.provider == provider,
            WebchatVoiceTranscriptSegment.provider_session_id == provider_session_id,
            WebchatVoiceTranscriptSegment.segment_id == segment_id,
            WebchatVoiceTranscriptSegment.participant_identity == participant_identity,
        )
        .one_or_none()
    )
    created = False
    if segment is None:
        segment = WebchatVoiceTranscriptSegment(
            voice_session_id=session.id,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            provider=provider,
            provider_session_id=provider_session_id,
            provider_item_id=f"{provider_session_id}:{segment_id}",
            participant_identity=participant_identity,
            speaker_type=CUSTOMER_SPEAKER_TYPE,
            speaker_label=CUSTOMER_SPEAKER_LABEL,
            segment_id=segment_id,
            language=stt_result.language,
            is_final=True,
            start_ms=None,
            end_ms=None,
            text_raw=stt_result.text_redacted or "",
            text_redacted=stt_result.text_redacted,
            confidence=stt_result.confidence,
            redaction_status="redacted",
            created_at=utc_now(),
        )
        db.add(segment)
        created = True
    else:
        segment.voice_session_id = session.id
        segment.conversation_id = session.conversation_id
        segment.ticket_id = session.ticket_id
        segment.language = stt_result.language
        segment.is_final = True
        segment.text_raw = stt_result.text_redacted or ""
        segment.text_redacted = stt_result.text_redacted
        segment.confidence = stt_result.confidence
        segment.redaction_status = "redacted"
    db.flush()
    return TranscriptWriteResult(segment=segment, created=created)


def _provider_session_id(session: WebchatVoiceSession, settings: WebCallAISettings) -> str:
    if settings.stt_transcript_provider_session_id_source != "voice_session_public_id":
        raise RuntimeError("WEBCALL_AI_STT_TRANSCRIPT_PROVIDER_SESSION_ID_SOURCE must be voice_session_public_id")
    return session.public_id


def _segment_id(session: WebchatVoiceSession, segment_index: int | None) -> str:
    index = segment_index if segment_index is not None else int(session.ai_turn_count or 0) + 1
    return f"ai-stt-{index}"
