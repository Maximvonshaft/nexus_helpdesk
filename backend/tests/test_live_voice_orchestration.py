from __future__ import annotations

import hashlib
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.webchat_live_voice import _connection_ticket, _upstream_ws_url, _valid_connection_ticket  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Team, Ticket  # noqa: E402
from app.services import live_voice_orchestration_service as voice_service  # noqa: E402
from app.services.webchat_ai_service import AI_AUTHOR_LABEL  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.voice_models import WebchatVoiceParticipant, WebchatVoiceSession, WebchatVoiceTranscriptSegment  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'live_voice_orchestration.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _conversation(db_session) -> tuple[WebchatConversation, WebchatVoiceSession]:
    suffix = uuid.uuid4().hex[:10]
    team = Team(name=f"Voice-{suffix}", team_type="support")
    ticket = Ticket(
        ticket_no=f"VOICE-{suffix}",
        title="Voice customer request",
        description="Voice customer request",
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.ai_active,
    )
    db_session.add_all([team, ticket])
    db_session.flush()
    ticket.team_id = team.id
    conversation = WebchatConversation(
        public_id=f"wc_{suffix}",
        visitor_token_hash=hashlib.sha256(b"visitor-token").hexdigest(),
        tenant_key="default",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="Voice visitor",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    session = WebchatVoiceSession(
        public_id=f"voice_{suffix}",
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        provider=voice_service.VOICE_PROVIDER,
        provider_room_name=f"voice_{suffix}",
        status="active",
        mode=voice_service.VOICE_MODE,
        transcript_status="active",
        ai_agent_status="active",
        started_at=utc_now(),
        active_at=utc_now(),
    )
    db_session.add(session)
    db_session.flush()
    return conversation, session


def test_live_voice_connection_ticket_is_scoped_and_expires() -> None:
    ticket = _connection_ticket(
        secret="shared-secret-for-live-voice-tests",
        conversation_id="wc-1",
        voice_session_id="voice-1",
        expires_at=4_102_444_800,
    )

    assert _valid_connection_ticket(
        ticket=ticket,
        secret="shared-secret-for-live-voice-tests",
        conversation_id="wc-1",
        voice_session_id="voice-1",
    )
    assert not _valid_connection_ticket(
        ticket=ticket,
        secret="shared-secret-for-live-voice-tests",
        conversation_id="wc-2",
        voice_session_id="voice-1",
    )


def test_voice_proxy_only_forwards_bounded_media_context() -> None:
    url = _upstream_ws_url(
        "ws://runtime.test/ws?fixed=1",
        "server-secret",
        {"conversation_id": "wc-1", "voice_session_id": "voice-1", "lang_code": "auto"},
    )

    assert "conversation_id=wc-1" in url
    assert "voice_session_id=voice-1" in url
    assert "token=server-secret" in url
    assert "visitor_token" not in url
    assert "connection_ticket" not in url


def test_new_runtime_voice_session_replaces_existing_active_session(db_session) -> None:
    conversation, existing_session = _conversation(db_session)
    existing_participant = WebchatVoiceParticipant(
        voice_session_id=existing_session.id,
        participant_type="visitor",
        provider_identity=f"visitor:{conversation.public_id}:old",
        status="joined",
        joined_at=utc_now(),
    )
    db_session.add(existing_participant)
    db_session.flush()

    replacement = voice_service.create_runtime_voice_session(
        db_session,
        conversation_public_id=conversation.public_id,
        visitor_token="visitor-token",
        locale="en",
        ttl_seconds=900,
    )

    assert replacement.id != existing_session.id
    assert replacement.status == "active"
    assert existing_session.status == "ended"
    assert existing_session.ended_at is not None
    assert existing_participant.status == "left"
    assert existing_participant.left_at is not None
    participant = db_session.query(WebchatVoiceParticipant).filter(
        WebchatVoiceParticipant.voice_session_id == replacement.id
    ).one()
    assert participant.status == "joined"


def test_expired_runtime_voice_session_is_ended_with_naive_database_timestamp(db_session) -> None:
    conversation, voice_session = _conversation(db_session)
    voice_session.expires_at = utc_now().replace(tzinfo=None) - timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(HTTPException, match="live voice session expired"):
        voice_service.authorize_runtime_voice_socket(
            db_session,
            conversation_public_id=conversation.public_id,
            voice_session_public_id=voice_session.public_id,
        )

    assert voice_session.status == "ended"


def test_voice_turn_uses_webchat_history_and_is_idempotent(db_session, monkeypatch) -> None:
    conversation, voice_session = _conversation(db_session)
    observed_history: list[list[str]] = []

    def fake_runtime(db, *, conversation_id, ticket_id, visitor_message_id):
        history = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.conversation_id == conversation_id)
            .order_by(WebchatMessage.id.asc())
            .all()
        )
        observed_history.append([row.body for row in history])
        visitor = db.query(WebchatMessage).filter(WebchatMessage.id == visitor_message_id).one()
        reply = WebchatMessage(
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            direction="agent",
            body=f"runtime:{visitor.body}",
            body_text=f"runtime:{visitor.body}",
            message_type="text",
            delivery_status="sent",
            author_label=AI_AUTHOR_LABEL,
        )
        db.add(reply)
        db.flush()
        return {
            "status": "done",
            "message_id": reply.id,
            "reply_source": "provider_runtime",
            "runtime_trace": {"runtime_trace_id": f"trace-{visitor.id}"},
        }

    monkeypatch.setattr(voice_service, "process_webchat_ai_reply_job", fake_runtime)

    first = voice_service.process_runtime_voice_turn(
        db_session,
        conversation_public_id=conversation.public_id,
        voice_session_public_id=voice_session.public_id,
        turn_id=1,
        transcript="My tracking number is CH020000129135",
        stt_language="en",
    )
    second = voice_service.process_runtime_voice_turn(
        db_session,
        conversation_public_id=conversation.public_id,
        voice_session_public_id=voice_session.public_id,
        turn_id=2,
        transcript="What is its status?",
        stt_language="en",
    )
    replay = voice_service.process_runtime_voice_turn(
        db_session,
        conversation_public_id=conversation.public_id,
        voice_session_public_id=voice_session.public_id,
        turn_id=2,
        transcript="What is its status?",
        stt_language="en",
    )

    assert first["reply"] == "runtime:My tracking number is CH020000129135"
    assert second["reply"] == "runtime:What is its status?"
    assert replay["idempotent"] is True
    assert observed_history[0] == ["My tracking number is CH020000129135"]
    assert observed_history[1] == [
        "My tracking number is CH020000129135",
        "runtime:My tracking number is CH020000129135",
        "What is its status?",
    ]
    messages = db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id).all()
    assert len(messages) == 4
    assert all(row.message_type == "voice_transcript" for row in messages)
    assert db_session.query(WebchatVoiceTranscriptSegment).filter(
        WebchatVoiceTranscriptSegment.voice_session_id == voice_session.id
    ).count() == 4


def test_voice_runtime_null_reply_is_not_spoken_or_persisted_as_ai_text(db_session, monkeypatch) -> None:
    conversation, voice_session = _conversation(db_session)

    monkeypatch.setattr(
        voice_service,
        "process_webchat_ai_reply_job",
        lambda *args, **kwargs: {"status": "null_reply", "reply_source": "provider_runtime"},
    )
    result = voice_service.process_runtime_voice_turn(
        db_session,
        conversation_public_id=conversation.public_id,
        voice_session_public_id=voice_session.public_id,
        turn_id=1,
        transcript="hello",
        stt_language="en",
    )

    assert result["reply"] is None
    assert result["status"] == "null_reply"
    assert db_session.query(WebchatMessage).filter(
        WebchatMessage.conversation_id == conversation.id,
        WebchatMessage.direction == "agent",
    ).count() == 0
