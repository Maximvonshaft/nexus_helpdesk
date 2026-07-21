from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_agent_run_events.db")

ROOT = Path(__file__).resolve().parents[1]

from app.db import Base  # noqa: E402
from app.models_agent_control import AgentRunEvent  # noqa: E402
from app.services.agent_runtime.run_events import (  # noqa: E402
    append_agent_event,
    finish_agent_run,
    start_agent_run,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-runs.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_agent_run_events_are_sequence_ordered_and_content_closed(db_session) -> None:
    run = start_agent_run(
        db_session,
        request_id="request-1",
        session_id="session-1",
        tenant_key="tenant-a",
        channel="webchat",
        environment="production",
        runtime_version="nexus.agent_runtime.v4",
    )
    event = append_agent_event(
        db_session,
        run=run,
        event_type="provider_completed",
        round_index=0,
        duration_ms=42,
        safe_payload={
            "provider": "private_ai_runtime",
            "round_index": 0,
            "elapsed_ms": 42,
            "model": "qwen",
            "usage": {"input_tokens": 12},
            "prompt": "must-not-persist",
            "tool_arguments": {"tracking_number": "CH020000129135"},
            "authorization": "Bearer secret",
        },
    )
    finish_agent_run(
        db_session,
        run=run,
        status="succeeded",
        final_action="reply",
        elapsed_ms=50,
        round_count=1,
    )
    db_session.commit()

    events = (
        db_session.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.sequence.asc())
        .all()
    )
    assert [row.sequence for row in events] == list(range(1, len(events) + 1))
    assert events[0].event_type == "run_started"
    assert event.safe_payload_json == {
        "provider": "private_ai_runtime",
        "round_index": 0,
        "elapsed_ms": 42,
        "model": "qwen",
        "usage": {"input_tokens": 12},
    }
    rendered = str([row.safe_payload_json for row in events])
    for forbidden in (
        "must-not-persist",
        "CH020000129135",
        "Bearer secret",
        "tool_arguments",
    ):
        assert forbidden not in rendered
    assert run.status == "succeeded"
    assert run.final_action == "reply"
    assert run.completed_at is not None


def test_agent_run_request_identity_is_idempotent(db_session) -> None:
    first = start_agent_run(
        db_session,
        request_id="request-2",
        session_id="session-2",
        tenant_key="tenant-a",
        channel="webchat",
        environment="test",
        runtime_version="nexus.agent_runtime.v4",
    )
    second = start_agent_run(
        db_session,
        request_id="request-2",
        session_id="session-2",
        tenant_key="tenant-a",
        channel="webchat",
        environment="test",
        runtime_version="nexus.agent_runtime.v4",
    )
    assert second.id == first.id
    with pytest.raises(RuntimeError, match="agent_run_idempotency_conflict"):
        start_agent_run(
            db_session,
            request_id="request-2",
            session_id="different-session",
            tenant_key="tenant-a",
            channel="webchat",
            environment="test",
            runtime_version="nexus.agent_runtime.v4",
        )


def test_unknown_event_type_is_rejected(db_session) -> None:
    run = start_agent_run(
        db_session,
        request_id="request-3",
        session_id="session-3",
        tenant_key="tenant-a",
        channel="webchat",
        environment="test",
        runtime_version="nexus.agent_runtime.v4",
    )
    with pytest.raises(RuntimeError, match="agent_run_event_type_invalid"):
        append_agent_event(
            db_session,
            run=run,
            event_type="model_thought",
            safe_payload={"thought": "never persist"},
        )
