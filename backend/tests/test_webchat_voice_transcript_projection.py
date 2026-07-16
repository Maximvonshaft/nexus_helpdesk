from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import ConversationState, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Team, Ticket  # noqa: E402
from app.services.webchat_performance import list_public_messages_throttled  # noqa: E402
from app.services.webchat_realtime_event_service import event_envelope, list_conversation_event_envelopes  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'webchat_voice_projection.db'}",
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


def _conversation(db_session) -> WebchatConversation:
    suffix = uuid.uuid4().hex[:10]
    team = Team(name=f"VoiceProjection-{suffix}", team_type="support")
    ticket = Ticket(
        ticket_no=f"VOICE-PROJECTION-{suffix}",
        title="Voice projection regression",
        description="Voice projection regression",
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
    return conversation


def _message(db_session, conversation: WebchatConversation, *, direction: str, body: str, message_type: str) -> WebchatMessage:
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction=direction,
        body=body,
        body_text=body,
        message_type=message_type,
        delivery_status="sent",
        author_label="Visitor" if direction == "visitor" else "Speedy",
    )
    db_session.add(message)
    db_session.flush()
    return message


def _message_event(db_session, conversation: WebchatConversation, message: WebchatMessage) -> WebchatEvent:
    event = WebchatEvent(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type="message.created",
        payload_json=json.dumps({"message_id": message.id}),
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_public_poll_excludes_voice_transcripts_but_keeps_text_messages(db_session) -> None:
    conversation = _conversation(db_session)
    _message(db_session, conversation, direction="visitor", body="spoken customer text", message_type="voice_transcript")
    _message(db_session, conversation, direction="agent", body="spoken AI text", message_type="voice_transcript")
    text_message = _message(db_session, conversation, direction="agent", body="typed reply", message_type="text")

    result = list_public_messages_throttled(db_session, conversation, after_id=0, limit=50)

    assert [message["id"] for message in result["messages"]] == [text_message.id]
    assert result["messages"][0]["message_type"] == "text"


def test_realtime_projection_hides_voice_transcripts_from_visitor_but_keeps_admin_audit(db_session) -> None:
    conversation = _conversation(db_session)
    voice_message = _message(
        db_session,
        conversation,
        direction="visitor",
        body="spoken customer text",
        message_type="voice_transcript",
    )
    event = _message_event(db_session, conversation, voice_message)

    visitor_projection = event_envelope(db_session, event, audience="visitor")
    admin_projection = event_envelope(db_session, event, audience="admin")

    assert visitor_projection is None
    assert admin_projection is not None
    assert admin_projection["message"]["id"] == voice_message.id
    assert admin_projection["message"]["message_type"] == "voice_transcript"


def test_realtime_replay_advances_past_hidden_voice_events_without_republishing_them(db_session) -> None:
    conversation = _conversation(db_session)
    voice_message = _message(
        db_session,
        conversation,
        direction="visitor",
        body="spoken customer text",
        message_type="voice_transcript",
    )
    voice_event = _message_event(db_session, conversation, voice_message)
    text_message = _message(db_session, conversation, direction="agent", body="typed reply", message_type="text")
    text_event = _message_event(db_session, conversation, text_message)

    replay = list_conversation_event_envelopes(
        db_session,
        conversation_id=conversation.id,
        after_id=0,
        audience="visitor",
    )

    assert [item["message"]["id"] for item in replay.events] == [text_message.id]
    assert replay.scanned_last_event_id == text_event.id
    assert replay.scanned_last_event_id > voice_event.id


def test_realtime_projection_keeps_normal_text_messages_visible_to_visitor(db_session) -> None:
    conversation = _conversation(db_session)
    text_message = _message(db_session, conversation, direction="agent", body="typed reply", message_type="text")
    event = _message_event(db_session, conversation, text_message)

    visitor_projection = event_envelope(db_session, event, audience="visitor")

    assert visitor_projection is not None
    assert visitor_projection["message"]["id"] == text_message.id
    assert visitor_projection["message"]["message_type"] == "text"
