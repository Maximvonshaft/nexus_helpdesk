from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession
from .audio_reference_resolver import resolve_audio_reference_for_session
from .config import get_webcall_ai_settings
from .lifecycle import WEBCALL_AI_STATUS_CLAIMED
from .media_schemas import WebCallSTTInput, WebCallTTSInput
from .orchestrator import run_webcall_ai_orchestrator
from .provider_router import get_stt_provider, get_tts_provider
from .stt_runtime import run_stt_runtime_for_session
from .transcript_writer import CUSTOMER_PARTICIPANT_IDENTITY

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
    transcript_segments: int = 0


def execute_mock_turn_for_claimed_session(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
) -> MockTurnExecutionResult:
    if session.ai_agent_status != WEBCALL_AI_STATUS_CLAIMED or session.ai_agent_worker_id != worker_id:
        raise ValueError("mock turn requires claimed WebCall AI session owned by worker")

    settings = get_webcall_ai_settings()
    transcript_segments = 0
    if settings.stt_runtime_enabled:
        stt_runtime_result = run_stt_runtime_for_session(
            db,
            session=session,
            worker_id=worker_id,
            participant_identity=CUSTOMER_PARTICIPANT_IDENTITY,
            settings=settings,
        )
        if not stt_runtime_result.usable:
            raise ValueError("STT runtime did not return a usable final transcript")
        stt_text_redacted = stt_runtime_result.text_redacted or ""
        stt_language = stt_runtime_result.language or "en"
        stt_confidence = stt_runtime_result.confidence
        stt_provider_name = stt_runtime_result.provider
        stt_events = stt_runtime_result.stt_events
        transcript_segments = 1 if stt_runtime_result.transcript_segment_id is not None else 0
    else:
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
        stt_text_redacted = stt_result.text_redacted
        stt_language = stt_result.language
        stt_confidence = stt_result.confidence
        stt_provider_name = stt_result.provider
        stt_events = stt_result.event_count

    if settings.orchestrator_enabled:
        orchestrator_result = run_webcall_ai_orchestrator(
            customer_text_redacted=stt_text_redacted,
            session=session,
            worker_id=worker_id,
            settings=settings,
        )
        ai_response_text_redacted = orchestrator_result.ai_response_text_redacted
        turn_intent = orchestrator_result.intent
        turn_action = orchestrator_result.action
        tracking_number_hash = orchestrator_result.tracking_number_hash
        handoff_required = orchestrator_result.handoff_required
        handoff_reason = orchestrator_result.handoff_reason
        nexus_decision = orchestrator_result.nexus_decision
        decision_reason = orchestrator_result.decision_reason
        speedaf_tool_name = orchestrator_result.speedaf_tool_name
        result_status = orchestrator_result.result_status
    else:
        ai_response_text_redacted = MOCK_AI_RESPONSE
        turn_intent = MOCK_INTENT
        turn_action = MOCK_ACTION
        tracking_number_hash = None
        handoff_required = False
        handoff_reason = None
        nexus_decision = "allowed"
        decision_reason = MOCK_DECISION_REASON
        speedaf_tool_name = None
        result_status = MOCK_RESULT_STATUS

    now = utc_now()
    next_turn_index = int(session.ai_turn_count or 0) + 1
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        turn_index=next_turn_index,
        customer_text_redacted=stt_text_redacted,
        ai_response_text_redacted=ai_response_text_redacted,
        language=stt_language,
        intent=turn_intent,
        action=turn_action,
        tracking_number_hash=tracking_number_hash,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        confidence=stt_confidence,
        provider="mock",
        stt_provider=stt_provider_name,
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
            text_redacted=ai_response_text_redacted,
            language=stt_language,
        )
    )
    if tts_result.synthesis_status != "mock_synthesized" and tts_result.synthesis_status != "ok":
        raise ValueError("TTS provider did not return usable synthesis metadata")

    action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=turn.id,
        model_action=turn_action,
        nexus_decision=nexus_decision,
        decision_reason=decision_reason,
        speedaf_tool_name=speedaf_tool_name,
        background_job_id=None,
        tool_call_log_id=None,
        result_status=result_status,
        created_at=now,
    )
    db.add(action)

    session.ai_turn_count = next_turn_index
    if not session.ai_language:
        session.ai_language = stt_language
    session.updated_at = now
    db.commit()
    db.refresh(turn)
    db.refresh(session)
    return MockTurnExecutionResult(
        turn=turn,
        stt_events=stt_events,
        tts_events=tts_result.event_count,
        transcript_segments=transcript_segments,
    )
