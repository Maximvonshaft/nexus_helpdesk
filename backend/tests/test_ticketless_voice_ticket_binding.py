from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_ticketless_voice_ticket_binding.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import (  # noqa: E402,F401
    models,
    models_agent_routing,
    models_control_plane,
    models_operations_dispatch,
    models_osr,
    operator_models,
    tool_models,
    voice_models,
    webchat_models,
)
from app.db import Base  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_agent_routing import ConversationControl  # noqa: E402
from app.services.conversation_first_service import (  # noqa: E402
    ensure_voice_ticket_for_public_conversation,
)
from app.services.webchat_service import _hash_token  # noqa: E402
from app.voice_models import WebchatVoiceSession  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ticketless_voice_binding.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _ticketless_conversation(db_session, *, token: str):
    customer = Customer(name="Voice Visitor", external_ref="voice-visitor")
    db_session.add(customer)
    db_session.flush()
    conversation = WebchatConversation(
        public_id="ticketless-voice-conversation",
        visitor_token_hash=_hash_token(token),
        tenant_key="default",
        channel_key="webchat",
        ticket_id=None,
        visitor_name=customer.name,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer.id,
            tenant_key="default",
            country_code="ME",
            channel_key="webchat",
        )
    )
    db_session.flush()
    return conversation


def test_voice_initiation_creates_one_ticket_and_repairs_active_session(db_session):
    token = "valid-voice-token"
    conversation = _ticketless_conversation(db_session, token=token)
    legacy_session = WebchatVoiceSession(
        public_id="legacy-ticketless-ringing-session",
        conversation_id=conversation.id,
        ticket_id=None,
        provider="mock",
        provider_room_name="legacy-room",
        status="ringing",
    )
    db_session.add(legacy_session)
    db_session.flush()
    assert db_session.query(Ticket).count() == 0

    first = ensure_voice_ticket_for_public_conversation(
        db_session,
        conversation_public_id=conversation.public_id,
        visitor_token=token,
    )
    second = ensure_voice_ticket_for_public_conversation(
        db_session,
        conversation_public_id=conversation.public_id,
        visitor_token=token,
    )

    db_session.refresh(conversation)
    db_session.refresh(legacy_session)
    assert db_session.query(Ticket).count() == 1
    assert first.id == second.id == conversation.ticket_id
    assert legacy_session.ticket_id == first.id
    assert first.customer_id == db_session.query(ConversationControl).one().customer_id


def test_invalid_voice_token_does_not_create_ticket(db_session):
    conversation = _ticketless_conversation(
        db_session,
        token="valid-voice-token",
    )

    with pytest.raises(HTTPException) as exc:
        ensure_voice_ticket_for_public_conversation(
            db_session,
            conversation_public_id=conversation.public_id,
            visitor_token="invalid-token",
        )

    assert exc.value.status_code == 403
    assert conversation.ticket_id is None
    assert db_session.query(Ticket).count() == 0
