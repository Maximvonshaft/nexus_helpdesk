from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment


@dataclass(frozen=True)
class WebCallAIEvidenceReport:
    ok: bool
    voice_session_public_id: str
    transcript_segment_count: int
    ai_turn_count: int
    ai_action_count: int
    handoff_required: bool
    tracking_hashes: list[str]
    safe_waybill_suffixes: list[str]
    providers: dict[str, str | None]
    failure_reasons: list[str]


def build_webcall_ai_evidence_report(
    db: Session,
    *,
    session: WebchatVoiceSession,
) -> WebCallAIEvidenceReport:
    turns = (
        db.query(WebchatVoiceAITurn)
        .filter(WebchatVoiceAITurn.voice_session_id == session.id)
        .order_by(WebchatVoiceAITurn.id.asc())
        .all()
    )
    actions = (
        db.query(WebchatVoiceAIAction)
        .filter(WebchatVoiceAIAction.voice_session_id == session.id)
        .order_by(WebchatVoiceAIAction.id.asc())
        .all()
    )
    transcript_count = (
        db.query(WebchatVoiceTranscriptSegment)
        .filter(WebchatVoiceTranscriptSegment.voice_session_id == session.id)
        .count()
    )
    tracking_hashes = sorted({turn.tracking_number_hash for turn in turns if turn.tracking_number_hash})
    providers = {
        "stt": _last_non_empty([turn.stt_provider for turn in turns]),
        "tts": _last_non_empty([turn.tts_provider for turn in turns]),
        "ai": _last_non_empty([turn.provider for turn in turns]),
    }
    failure_reasons = sorted(
        {
            value
            for value in [session.ai_agent_error_code, *[action.result_status for action in actions]]
            if value and value not in {"mock_turn_recorded", "tracking_fact_explained"}
        }
    )
    return WebCallAIEvidenceReport(
        ok=bool(turns or actions or transcript_count),
        voice_session_public_id=session.public_id,
        transcript_segment_count=transcript_count,
        ai_turn_count=len(turns),
        ai_action_count=len(actions),
        handoff_required=any(turn.handoff_required for turn in turns)
        or any(action.nexus_decision == "handoff" for action in actions),
        tracking_hashes=tracking_hashes,
        safe_waybill_suffixes=[item[-4:] for item in tracking_hashes if len(item) >= 4],
        providers=providers,
        failure_reasons=failure_reasons,
    )


def evidence_report_to_safe_dict(report: WebCallAIEvidenceReport) -> dict:
    return {
        "ok": report.ok,
        "voice_session_public_id": report.voice_session_public_id,
        "transcript_segment_count": report.transcript_segment_count,
        "ai_turn_count": report.ai_turn_count,
        "ai_action_count": report.ai_action_count,
        "handoff_required": report.handoff_required,
        "tracking_hashes": report.tracking_hashes,
        "safe_waybill_suffixes": report.safe_waybill_suffixes,
        "providers": report.providers,
        "failure_reasons": report.failure_reasons,
    }


def _last_non_empty(values: list[str | None]) -> str | None:
    for value in reversed(values):
        if value:
            return value
    return None
