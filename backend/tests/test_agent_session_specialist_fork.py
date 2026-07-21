from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import agent_runtime_operations as operations
from app.api.agent_runtime_operations import AgentRunForkRequest
from app.db import Base
from app.models_agent_control import AgentRunSnapshot
from app.services.agent_runtime import tool_adapter
from app.services.agent_runtime.run_events import (
    append_agent_event,
    finish_agent_run,
    start_agent_run,
)
from app.services.agent_runtime.specialist_service import run_read_only_specialists
from app.services.agent_runtime.tool_adapter import AgentExecutionContext
from app.services.ai_runtime.schemas import RuntimeAIProviderResult
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-specialists.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(engine)
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_read_only_specialists_use_only_safe_run_and_release_evidence(db_session) -> None:
    run = start_agent_run(
        db_session,
        request_id="specialist-parent",
        session_id="specialist-session",
        tenant_key="tenant-a",
        channel="webchat",
        environment="production",
        runtime_version="nexus.agent_runtime.v4",
    )
    run.release_id = 7
    run.release_digest = "a" * 64
    db_session.add(
        AgentRunSnapshot(
            request_id=run.request_id,
            session_id=run.session_id,
            tenant_key=run.tenant_key,
            deployment_id=3,
            release_id=7,
            snapshot_sha256="b" * 64,
            snapshot_json={
                "resolved": {
                    "knowledge": [
                        {"item_key": "shipping.status", "version": 2},
                    ]
                }
            },
            source="deployment",
        )
    )
    append_agent_event(
        db_session,
        run=run,
        event_type="tool_failed",
        safe_payload={
            "tool_name": "knowledge.search",
            "round_index": 0,
            "status": "failed",
            "elapsed_ms": 12,
            "error_code": "knowledge_not_found",
        },
    )
    append_agent_event(
        db_session,
        run=run,
        event_type="reply_finalized",
        safe_payload={
            "round_index": 1,
            "intent": "shipment_tracking",
            "handoff_required": False,
            "reply_chars": 90,
        },
    )
    finish_agent_run(
        db_session,
        run=run,
        status="fallback",
        final_action="fallback",
        elapsed_ms=40,
        error_code="knowledge_not_found",
        round_count=2,
    )

    results = run_read_only_specialists(
        db_session,
        parent_run=run,
        specialists=[
            "knowledge_researcher",
            "policy_reviewer",
            "case_summarizer",
        ],
    )
    assert [item["specialist"] for item in results] == [
        "knowledge_researcher",
        "policy_reviewer",
        "case_summarizer",
    ]
    assert results[0]["findings"][0]["evidence_refs"]
    assert results[1]["needs_human_review"] is True
    rendered = str(results).lower()
    for forbidden in (
        "customer_message",
        "customer_reply",
        "tool_arguments",
        "tracking_number",
        "hidden reasoning",
    ):
        assert forbidden not in rendered


def test_tool_worker_owns_a_fresh_sqlalchemy_session(monkeypatch, tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tool-worker.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    outer = Session(bind=engine, future=True)
    worker = Mock(spec=Session)
    monkeypatch.setattr(tool_adapter, "SessionLocal", lambda: worker)
    observed = {}

    def execute_with_db(db, **kwargs):
        observed["db"] = db
        observed["kwargs"] = kwargs
        return []

    monkeypatch.setattr(tool_adapter, "_execute_with_db", execute_with_db)
    try:
        result = tool_adapter.execute_agent_tool_calls(
            outer,
            calls=[],
            context=AgentExecutionContext(
                tenant_key="tenant-a",
                channel_key="webchat",
                session_id="session",
                request_id="request",
                customer_message="test",
            ),
        )
    finally:
        outer.close()
        engine.dispose()

    assert result == []
    assert observed["db"] is worker
    worker.commit.assert_called_once()
    worker.close.assert_called_once()
    assert worker.rollback.call_count == 0


@pytest.mark.asyncio
async def test_exact_snapshot_fork_is_read_only_and_parent_linked(monkeypatch) -> None:
    parent = SimpleNamespace(
        id=10,
        request_id="parent-request",
        session_id="parent-session",
        tenant_key="tenant-a",
        trace_id="a" * 64,
        deployment_id=2,
        release_id=3,
        release_digest="c" * 64,
        parent_run_id=None,
        fork_kind=None,
        status="succeeded",
        final_action="reply",
        error_code=None,
        elapsed_ms=20,
        started_at=None,
        completed_at=None,
    )
    snapshot = SimpleNamespace(
        id=7,
        snapshot_sha256="d" * 64,
        source="deployment",
        created_at=None,
    )
    monkeypatch.setattr(operations, "ensure_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        operations,
        "authoritative_tenant_key",
        lambda *_args, **_kwargs: "tenant-a",
    )
    monkeypatch.setattr(operations, "_run_or_404", lambda *_args, **_kwargs: parent)
    monkeypatch.setattr(
        operations,
        "_snapshot_for_run",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        operations,
        "build_agent_context",
        lambda *_args, **_kwargs: {
            "agent_release_digest": "d" * 64,
            "agent_execution_context": {},
            "channel_context": {},
        },
    )
    monkeypatch.setattr(
        operations,
        "run_read_only_specialists",
        lambda *_args, **_kwargs: [
            {
                "specialist": "case_summarizer",
                "summary": "Safe evidence.",
                "findings": [],
                "risks": [],
                "needs_human_review": False,
            }
        ],
    )
    captured = {}

    async def run_agent(_db, *, request):
        captured["request"] = request
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            raw_provider="private_ai_runtime",
            raw_payload_safe_summary={"agent_run_id": 11},
            reply="Reviewed safely.",
            intent="general_support",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_calls=[],
            elapsed_ms=5,
            error_code=None,
            retry_after_ms=None,
        )

    monkeypatch.setattr(operations, "run_agent_with_db", run_agent)
    result = await operations.fork_agent_run(
        10,
        AgentRunForkRequest(
            tenant_key="tenant-a",
            body="Review this test case.",
            channel="webchat",
            specialists=["case_summarizer"],
            execute_model=True,
        ),
        db=Mock(),
        current_user=SimpleNamespace(id=5),
    )

    request = captured["request"]
    assert result["agent_run_id"] == 11
    assert request.metadata["agent_parent_run_id"] == 10
    assert request.metadata["agent_fork_kind"] == "replay"
    assert request.metadata["agent_release_digest"] == "d" * 64
    assert request.metadata["channel_context"]["specialist_evidence"]
    assert all(
        get_tool_contract(name) is not None
        and get_tool_contract(name).is_read_tool
        for name in request.metadata["agent_allowed_tools"]
    )


@pytest.mark.asyncio
async def test_fork_rejects_any_release_snapshot_mismatch(monkeypatch) -> None:
    parent = SimpleNamespace(
        id=10,
        request_id="parent-request",
        session_id="parent-session",
        tenant_key="tenant-a",
        trace_id="a" * 64,
        deployment_id=2,
        release_id=3,
        release_digest="c" * 64,
        parent_run_id=None,
        fork_kind=None,
        status="succeeded",
        final_action="reply",
        error_code=None,
        elapsed_ms=20,
        started_at=None,
        completed_at=None,
    )
    snapshot = SimpleNamespace(id=7, snapshot_sha256="d" * 64)
    monkeypatch.setattr(operations, "ensure_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        operations,
        "authoritative_tenant_key",
        lambda *_args, **_kwargs: "tenant-a",
    )
    monkeypatch.setattr(operations, "_run_or_404", lambda *_args, **_kwargs: parent)
    monkeypatch.setattr(
        operations,
        "_snapshot_for_run",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        operations,
        "build_agent_context",
        lambda *_args, **_kwargs: {"agent_release_digest": "e" * 64},
    )

    with pytest.raises(HTTPException) as exc:
        await operations.fork_agent_run(
            10,
            AgentRunForkRequest(body="Test", execute_model=False),
            db=Mock(),
            current_user=SimpleNamespace(id=5),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "agent_fork_exact_release_not_resolved"
