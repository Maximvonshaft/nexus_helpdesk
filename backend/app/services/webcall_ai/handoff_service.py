from __future__ import annotations

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


def mark_webcall_ai_handoff_required(
    db: Session,
    *,
    session: WebchatVoiceSession,
    turn: WebchatVoiceAITurn | None,
    reason: str,
    worker_id: str,
) -> WebchatVoiceAIAction:
    existing = (
        db.query(WebchatVoiceAIAction)
        .filter(
            WebchatVoiceAIAction.voice_session_id == session.id,
            WebchatVoiceAIAction.model_action == "handoff_to_human",
            WebchatVoiceAIAction.result_status == "handoff_required",
            WebchatVoiceAIAction.decision_reason == reason[:240],
        )
        .first()
    )
    if existing is not None:
        return existing

    action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=turn.id if turn is not None else None,
        model_action="handoff_to_human",
        nexus_decision="handoff",
        decision_reason=reason[:240],
        speedaf_tool_name=None,
        background_job_id=None,
        tool_call_log_id=None,
        result_status="handoff_required",
        created_at=utc_now(),
    )
    session.ai_handoff_reason = reason[:240]
    session.ai_agent_worker_id = session.ai_agent_worker_id or worker_id
    session.updated_at = utc_now()
    db.add(action)
    db.commit()
    db.refresh(action)
    return action
