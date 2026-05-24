from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment
from .event_service import write_event
from .providers.base import LLMResult, STTResult, TTSResult

_TRACKING_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9\- ]{5,40}\b", re.I)


@dataclass(frozen=True)
class PersistedTurnEvidence:
    turn: WebchatVoiceAITurn
    action: WebchatVoiceAIAction
    transcript: WebchatVoiceTranscriptSegment


def hash_tracking_number(value: str | None) -> str | None:
    normalized = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def redact_customer_text(text: str | None) -> str:
    return _TRACKING_TOKEN_RE.sub(lambda match: _redact_token(match.group(0)), text or "")


def _redact_token(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    if len(normalized) < 6:
        return "[redacted]"
    return f"{normalized[:3]}...{normalized[-2:]}"


def persist_turn_evidence(
    db: Session,
    *,
    session: WebchatVoiceSession,
    stt: STTResult,
    llm: LLMResult,
    tts: TTSResult,
    tool_result: dict | None,
    tracking_number: str | None,
    latency_ms: int | None,
) -> PersistedTurnEvidence:
    turn_index = int(session.ai_turn_count or 0) + 1
    customer_text_redacted = redact_customer_text(stt.text)
    transcript = WebchatVoiceTranscriptSegment(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        provider=stt.provider_name or "unknown",
        provider_session_id=session.public_id,
        provider_item_id=f"{session.public_id}:turn:{turn_index}:stt",
        participant_identity="visitor",
        speaker_type="visitor",
        speaker_label="Customer",
        segment_id=f"webcall-ai-turn-{turn_index}",
        language=stt.language,
        is_final=True,
        text_raw=customer_text_redacted,
        text_redacted=customer_text_redacted,
        confidence=stt.confidence,
        redaction_status="redacted",
        created_at=utc_now(),
    )
    db.add(transcript)
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        turn_index=turn_index,
        customer_text_redacted=customer_text_redacted,
        ai_response_text_redacted=llm.response_text,
        language=stt.language,
        intent=llm.intent,
        action="handoff_to_human" if llm.handoff_required else llm.intent,
        tracking_number_hash=hash_tracking_number(tracking_number),
        handoff_required=llm.handoff_required,
        handoff_reason=llm.handoff_reason,
        confidence=stt.confidence,
        provider=llm.provider_name,
        stt_provider=stt.provider_name,
        tts_provider=tts.provider_name,
        latency_ms=latency_ms,
        created_at=utc_now(),
    )
    db.add(turn)
    db.flush()
    action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=turn.id,
        model_action=llm.intent,
        nexus_decision="handoff" if llm.handoff_required else "allowed",
        decision_reason=llm.handoff_reason or "read_only_tracking_flow",
        speedaf_tool_name="tracking_lookup" if tool_result else None,
        result_status=(tool_result or {}).get("result", {}).get("status") if isinstance(tool_result, dict) else None,
        created_at=utc_now(),
    )
    db.add(action)
    session.ai_turn_count = turn_index
    session.ai_language = stt.language or session.ai_language
    if llm.handoff_required:
        session.ai_handoff_reason = llm.handoff_reason
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.transcript.final",
        payload={"voice_session_id": session.public_id, "turn_index": turn_index, "text_redacted": customer_text_redacted},
    )
    if tool_result:
        write_event(
            db,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type="webcall_ai.tool.called",
            payload={"voice_session_id": session.public_id, "turn_index": turn_index, "tool": "tracking_lookup", "result": tool_result},
        )
    if llm.handoff_required:
        write_event(
            db,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type="webcall_ai.handoff.requested",
            payload={"voice_session_id": session.public_id, "turn_index": turn_index, "reason": llm.handoff_reason or "handoff_required"},
        )
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.response.spoken",
        payload={"voice_session_id": session.public_id, "turn_index": turn_index, "tts_provider": tts.provider_name, "mime_type": tts.mime_type},
    )
    db.flush()
    return PersistedTurnEvidence(turn=turn, action=action, transcript=transcript)
