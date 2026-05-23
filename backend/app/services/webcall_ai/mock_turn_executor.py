from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession
from .audio_reference_resolver import resolve_audio_reference_for_session
from .lifecycle import WEBCALL_AI_STATUS_CLAIMED
from .media_schemas import WebCallSTTInput, WebCallTTSInput
from .provider_router import get_stt_provider, get_tts_provider

MOCK_AI_RESPONSE = "Hello, this is Speedaf AI support. Please provide your tracking number."
MOCK_ACTION = "ask_tracking_number"
MOCK_INTENT = "tracking_missing_number"
MOCK_DECISION_REASON = "pr3_deterministic_mock_turn_no_external_effect"
MOCK_RESULT_STATUS = "mock_turn_recorded"


@dataclass(frozen=True)
class MockTurnExecutionResult:
    turn: WebchatVoiceAITurn
    stt_events: int
    tts_events: int


def execute_mock_turn_for_claimed_session(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
) -> MockTurnExecutionResult:
    if session.ai_agent_status != WEBCALL_AI_STATUS_CLAIMED or session.ai_agent_worker_id != worker_id:
        raise ValueError("mock turn requires claimed WebCall AI session owned by worker")

    stt_provider = get_stt_provider()
    audio_reference = resolve_audio_reference_for_session(session, worker_id)
    stt_result = stt_provider.transcribe(
        WebCallSTTInput(
            voice_session_id=session.id,
            worker_id=worker_id,
            locale=session.ai_language,
            audio_reference=audio_reference,
        )
    )
    if (
        stt_result.status != "ok"
        or not stt_result.is_final
        or not stt_result.text_redacted
        or not stt_result.language
        or stt_result.confidence is None
    ):
        raise ValueError("STT provider did not return a usable final transcript")

    now = utc_now()
    next_turn_index = int(session.ai_turn_count or 0) + 1
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        turn_index=next_turn_index,
        customer_text_redacted=stt_result.text_redacted,
        ai_response_text_redacted=MOCK_AI_RESPONSE,
        language=stt_result.language,
        intent=MOCK_INTENT,
        action=MOCK_ACTION,
        tracking_number_hash=None,
        handoff_required=False,
        handoff_reason=None,
        confidence=stt_result.confidence,
        provider="mock",
        stt_provider=stt_result.provider,
        tts_provider="mock",
        latency_ms=0,
        created_at=now,
    )
    db.add(turn)
    db.flush()

    tts_provider = get_tts_provider()
    tts_result = tts_provider.synthesize(
        WebCallTTSInput(
            voice_session_id=session.id,
            worker_id=worker_id,
            text_redacted=MOCK_AI_RESPONSE,
            language=stt_result.language,
        )
    )
    if tts_result.synthesis_status != "mock_synthesized" and tts_result.synthesis_status != "ok":
        raise ValueError("TTS provider did not return usable synthesis metadata")

    action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=turn.id,
        model_action=MOCK_ACTION,
        nexus_decision="allowed",
        decision_reason=MOCK_DECISION_REASON,
        speedaf_tool_name=None,
        background_job_id=None,
        tool_call_log_id=None,
        result_status=MOCK_RESULT_STATUS,
        created_at=now,
    )
    db.add(action)

    session.ai_turn_count = next_turn_index
    if not session.ai_language:
        session.ai_language = stt_result.language
    session.updated_at = now
    db.commit()
    db.refresh(turn)
    db.refresh(session)
    return MockTurnExecutionResult(
        turn=turn,
        stt_events=stt_result.event_count,
        tts_events=tts_result.event_count,
    )
