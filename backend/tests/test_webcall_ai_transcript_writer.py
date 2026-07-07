import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_transcript_writer_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.media_schemas import WebCallSTTResult
from app.services.webcall_ai.transcript_writer import CUSTOMER_PARTICIPANT_IDENTITY, write_stt_transcript_segment
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession, WebchatVoiceTranscriptSegment


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_AI_STT_TRANSCRIPT_PROVIDER_SESSION_ID_SOURCE",
    ]:
        monkeypatch.delenv(key, raising=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _voice_session(db) -> WebchatVoiceSession:
    now = utc_now()
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
    db.commit()
    db.refresh(session)
    return session


def _stt_result(text: str = "Please track my parcel.") -> WebCallSTTResult:
    return WebCallSTTResult(
        text_redacted=text,
        language="en",
        confidence=97,
        is_final=True,
        provider="mock",
    )


def test_write_stt_transcript_segment_stores_redacted_customer_segment(db):
    session = _voice_session(db)

    result = write_stt_transcript_segment(
        db,
        session=session,
        stt_result=_stt_result(),
        participant_identity=CUSTOMER_PARTICIPANT_IDENTITY,
    )
    db.commit()
    segment = db.query(WebchatVoiceTranscriptSegment).one()

    assert result.created is True
    assert segment.provider == "mock"
    assert segment.provider_session_id == session.public_id
    assert segment.provider_item_id == f"{session.public_id}:ai-stt-1"
    assert segment.participant_identity == "visitor"
    assert segment.speaker_type == "visitor"
    assert segment.speaker_label == "Customer"
    assert segment.segment_id == "ai-stt-1"
    assert segment.language == "en"
    assert segment.is_final is True
    assert segment.text_raw == "Please track my parcel."
    assert segment.text_redacted == "Please track my parcel."
    assert segment.confidence == 97
    assert segment.redaction_status == "redacted"


def test_write_stt_transcript_segment_is_idempotent(db):
    session = _voice_session(db)

    first = write_stt_transcript_segment(db, session=session, stt_result=_stt_result("First text."))
    second = write_stt_transcript_segment(db, session=session, stt_result=_stt_result("Updated text."))
    db.commit()

    assert first.created is True
    assert second.created is False
    assert first.segment.id == second.segment.id
    assert db.query(WebchatVoiceTranscriptSegment).count() == 1
    assert db.query(WebchatVoiceTranscriptSegment).one().text_redacted == "Updated text."
