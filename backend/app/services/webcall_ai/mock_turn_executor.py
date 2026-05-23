from __future__ import annotations

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession
from .lifecycle import WEBCALL_AI_STATUS_CLAIMED

MOCK_AI_RESPONSE = "Hello, this is Speedaf AI support. Please provide your tracking number."
MOCK_ACTION = "ask_tracking_number"
MOCK_INTENT = "tracking_missing_number"
MOCK_DECISION_REASON = "pr3_deterministic_mock_turn_no_external_effect"
MOCK_RESULT_STATUS = "mock_turn_recorded"


def execute_mock_turn_for_claimed_session(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
) -> WebchatVoiceAITurn:
    if session.ai_agent_status != WEBCALL_AI_STATUS_CLAIMED or session.ai_agent_worker_id != worker_id:
        raise ValueError("mock turn requires claimed WebCall AI session owned by worker")

    now = utc_now()
    next_turn_index = int(session.ai_turn_count or 0) + 1
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        turn_index=next_turn_index,
        customer_text_redacted=None,
        ai_response_text_redacted=MOCK_AI_RESPONSE,
        language="en",
        intent=MOCK_INTENT,
        action=MOCK_ACTION,
        tracking_number_hash=None,
        handoff_required=False,
        handoff_reason=None,
        confidence=100,
        provider="mock",
        stt_provider="mock",
        tts_provider="mock",
        latency_ms=0,
        created_at=now,
    )
    db.add(turn)
    db.flush()

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
        session.ai_language = "en"
    session.updated_at = now
    db.commit()
    db.refresh(turn)
    db.refresh(session)
    return turn
