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
from app.model_registry import REPRESENTATIVE_TABLES, declared_model_modules  # noqa: E402
from app.models_agent_control import AgentRunEvent  # noqa: E402
from app.models_agent_runtime import AgentSessionCheckpoint  # noqa: E402
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


def test_agent_runtime_models_are_registered_by_the_single_model_authority() -> None:
    assert "app.models_agent_runtime" in declared_model_modules()
    assert (
        REPRESENTATIVE_TABLES["app.models_agent_runtime"]
        == "agent_session_checkpoints"
    )


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
    context_event = append_agent_event(
        db_session,
        run=run,
        event_type="context_compiled",
        safe_payload={
            "budget_chars": 12000,
            "prompt_chars": 3200,
            "estimated_tokens": 800,
            "compacted": True,
            "omitted_sections": ["recent_conversation"],
            "digest": "a" * 64,
            "prompt": "must-not-persist",
        },
    )
    reply_event = append_agent_event(
        db_session,
        run=run,
        event_type="reply_finalized",
        safe_payload={
            "round_index": 0,
            "intent": "general_support",
            "handoff_required": False,
            "reply_chars": 42,
            "reply": "must-not-persist",
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
    assert context_event.safe_payload_json["prompt_chars"] == 3200
    assert reply_event.safe_payload_json["reply_chars"] == 42
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


def test_agent_run_request_identity_is_idempotent_only_while_running(db_session) -> None:
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
    finish_agent_run(
        db_session,
        run=first,
        status="succeeded",
        final_action="reply",
        elapsed_ms=10,
    )
    with pytest.raises(RuntimeError, match="agent_run_already_terminal"):
        start_agent_run(
            db_session,
            request_id="request-2",
            session_id="session-2",
            tenant_key="tenant-a",
            channel="webchat",
            environment="test",
            runtime_version="nexus.agent_runtime.v4",
        )


def test_terminal_run_atomically_persists_bounded_session_checkpoint(db_session) -> None:
    run = start_agent_run(
        db_session,
        request_id="request-checkpoint",
        session_id="session-checkpoint",
        tenant_key="tenant-a",
        channel="webchat",
        environment="production",
        runtime_version="nexus.agent_runtime.v4",
    )
    # SQLite test connections do not enforce the foreign key by default. Runtime
    # binds this value only from an authoritative AgentRelease.
    run.release_id = 7
    append_agent_event(
        db_session,
        run=run,
        event_type="reply_finalized",
        safe_payload={
            "round_index": 1,
            "intent": "shipment_tracking",
            "handoff_required": False,
            "reply_chars": 100,
        },
    )
    append_agent_event(
        db_session,
        run=run,
        event_type="tool_completed",
        safe_payload={
            "tool_name": "speedaf.order.query",
            "round_index": 0,
            "status": "executed",
            "elapsed_ms": 15,
            "ok": True,
        },
    )
    finish_agent_run(
        db_session,
        run=run,
        status="succeeded",
        final_action="reply",
        elapsed_ms=30,
        round_count=2,
    )

    checkpoint = (
        db_session.query(AgentSessionCheckpoint)
        .filter(
            AgentSessionCheckpoint.tenant_key == "tenant-a",
            AgentSessionCheckpoint.session_id == "session-checkpoint",
            AgentSessionCheckpoint.is_active.is_(True),
        )
        .one()
    )
    assert checkpoint.release_id == 7
    assert checkpoint.summary_json["last_intent"] == "shipment_tracking"
    assert checkpoint.summary_json["last_final_action"] == "reply"
    assert checkpoint.summary_json["tool_outcomes"] == [
        {
            "tool_name": "speedaf.order.query",
            "status": "executed",
            "ok": True,
            "error_code": None,
        }
    ]
    rendered = str(checkpoint.summary_json).lower()
    assert "reply_chars" not in rendered
    assert "tracking_number" not in rendered


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
