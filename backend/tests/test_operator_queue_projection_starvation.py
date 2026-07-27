from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/operator_queue_projection_starvation.db",
)

from app.db import Base
from app.enums import (
    SourceChannel,
    TicketPriority,
    TicketSource,
)
from app.models import Ticket
from app.operator_models import OperatorTask
from app.services.operator_queue import project_webchat_handoff_tasks
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatHandoffRequest


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'projection-starvation.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _source(db, index: int):
    ticket = Ticket(
        ticket_no=f"STARVE-{index}",
        title="Projection starvation proof",
        description="Projection starvation proof",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"starve-{index}",
        visitor_token_hash=f"hash-{index}",
        tenant_key="default",
        channel_key="web_chat",
        ticket_id=ticket.id,
    )
    db.add(conversation)
    db.flush()
    now = utc_now()
    handoff = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        source="system",
        trigger_type="handoff_required",
        status="requested",
        reason_code="starvation_proof",
        requested_by_actor_type="system",
        requested_at=now,
        lock_version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(handoff)
    db.flush()
    conversation.current_handoff_request_id = handoff.id
    return ticket, conversation, handoff


def test_missing_projection_is_selected_after_many_current_rows(db_session):
    current = []
    for index in range(1, 31):
        ticket, conversation, handoff = _source(db_session, index)
        db_session.add(
            OperatorTask(
                source_type="webchat_handoff",
                source_id=str(handoff.id),
                source_version=handoff.lock_version,
                projection_schema="nexus.operator-task.webchat-handoff.v1",
                ticket_id=ticket.id,
                webchat_conversation_id=conversation.id,
                task_type="handoff",
                status="pending",
                priority=40,
            )
        )
        current.append(handoff.id)
    _ticket, missing_conversation, missing_handoff = _source(db_session, 31)
    db_session.commit()

    result = project_webchat_handoff_tasks(db_session, limit=1)

    assert result.created == 1
    projected = (
        db_session.query(OperatorTask)
        .filter(
            OperatorTask.webchat_conversation_id == missing_conversation.id
        )
        .one()
    )
    assert projected.source_id == str(missing_handoff.id)
    assert db_session.query(OperatorTask).count() == 31
