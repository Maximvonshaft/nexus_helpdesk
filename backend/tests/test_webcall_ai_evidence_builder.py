import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_evidence_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webcall_ai.evidence_builder import build_webcall_ai_evidence_report, evidence_report_to_safe_dict
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_evidence_report_excludes_text_tokens_raw_audio_and_full_tracking(db):
    now = utc_now()
    full_tracking = "CH020000008030"
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    transcript = WebchatVoiceTranscriptSegment(
        voice_session_id=session.id,
        conversation_id=1,
        ticket_id=1,
        provider="mock",
        provider_session_id=session.public_id,
        participant_identity="visitor",
        speaker_type="visitor",
        segment_id="seg-1",
        language="en",
        is_final=True,
        text_raw=f"raw transcript {full_tracking} token",
        text_redacted=f"redacted transcript {full_tracking}",
        redaction_status="redacted",
        created_at=now,
    )
    turn = WebchatVoiceAITurn(
        voice_session_id=session.id,
        conversation_id=1,
        ticket_id=1,
        turn_index=1,
        customer_text_redacted=f"Where is {full_tracking}?",
        ai_response_text_redacted="Safe reply",
        tracking_number_hash=hash_tracking_number(full_tracking),
        stt_provider="mock",
        tts_provider="mock",
        provider="mock",
        created_at=now,
    )
    db.add_all([transcript, turn])
    db.flush()
    db.add(
        WebchatVoiceAIAction(
            voice_session_id=session.id,
            turn_id=turn.id,
            model_action="explain_tracking_fact",
            nexus_decision="allowed",
            speedaf_tool_name="speedaf.order.query",
            result_status="tracking_fact_explained",
            created_at=now,
        )
    )
    db.commit()

    report = build_webcall_ai_evidence_report(db, session=session)
    payload = evidence_report_to_safe_dict(report)
    rendered = repr(payload).lower()

    assert payload["transcript_segment_count"] == 1
    assert payload["ai_turn_count"] == 1
    assert payload["ai_action_count"] == 1
    assert full_tracking.lower() not in rendered
    assert "raw transcript" not in rendered
    assert "safe reply" not in rendered
    assert "token" not in rendered
    assert "raw_audio" not in rendered
