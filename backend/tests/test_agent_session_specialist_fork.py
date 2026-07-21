from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import agent_runtime_operations as operations
from app.api.agent_runtime_operations import AgentRunForkRequest
from app.services.agent_runtime import specialist_runtime, tool_adapter
from app.services.agent_runtime.tool_adapter import AgentExecutionContext
from app.services.agent_tool_contracts import bootstrap_agent_tool_contracts
from app.services.ai_runtime.schemas import RuntimeAIProviderResult
from app.services.provider_runtime.schemas import ProviderResult
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract


def _release_snapshot() -> dict:
    return {
        "source": "deployment",
        "tenant_key": "tenant-a",
        "release": {"id": 7, "version": 2, "manifest_sha256": "a" * 64},
        "resolved": {
            "resources": [
                {
                    "config_type": "runtime_policy",
                    "content": {"provider_timeout_ms": 5000},
                }
            ]
        },
    }


def test_specialist_delegate_is_one_canonical_read_only_tool_contract() -> None:
    bootstrap_agent_tool_contracts()
    contract = get_tool_contract("specialist.delegate")
    assert contract is not None
    assert contract.is_read_tool is True
    assert contract.controlled_action_required is True
    assert contract.customer_visible_result is False
    assert contract.risk_level == "medium"


@pytest.mark.asyncio
async def test_specialist_runtime_uses_canonical_provider_router(monkeypatch) -> None:
    captured = {}

    async def route(_self, request):
        captured["request"] = request
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            elapsed_ms=4,
            structured_output={
                "specialist": "case_summarizer",
                "summary": "The bounded evidence is consistent.",
                "findings": [
                    {
                        "claim": "A safe event reference is available.",
                        "confidence": 1.0,
                        "evidence_refs": ["agent_run_event:12"],
                    }
                ],
                "risks": [],
                "recommended_action": "Return evidence to the parent Agent.",
                "needs_human_review": False,
            },
            raw_payload_safe_summary={"provider": "private_ai_runtime"},
        )

    monkeypatch.setattr(
        specialist_runtime.ProviderRuntimeRouter,
        "route",
        route,
    )
    result = await specialist_runtime.run_specialist(
        None,
        release_snapshot=_release_snapshot(),
        tenant_key="tenant-a",
        channel_key="webchat",
        session_id="session-a",
        request_id="specialist-request",
        specialist="case_summarizer",
        task="Summarize only the supplied evidence.",
        evidence_refs=["agent_run_event:12"],
    )

    assert result.ok is True
    assert result.evidence["specialist"] == "case_summarizer"
    assert captured["request"].scenario == "agent_specialist"
    assert captured["request"].output_contract == "nexus.agent_specialist.v1"
    assert captured["request"].metadata["agent_release_snapshot"]["release"]["id"] == 7


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
async def test_exact_snapshot_fork_is_read_only_parent_linked_and_requests_canonical_specialist(
    monkeypatch,
) -> None:
    bootstrap_agent_tool_contracts()
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
    assert result["requested_specialists"] == ["case_summarizer"]
    assert request.metadata["agent_parent_run_id"] == 10
    assert request.metadata["agent_fork_kind"] == "replay"
    assert request.metadata["agent_release_digest"] == "d" * 64
    assert request.metadata["channel_context"]["requested_specialists"] == [
        "case_summarizer"
    ]
    assert request.metadata["channel_context"]["specialist_delegation_tool"] == (
        "specialist.delegate"
    )
    assert all(
        get_tool_contract(name) is not None
        and get_tool_contract(name).is_read_tool
        for name in request.metadata["agent_allowed_tools"]
    )
    assert "integration.write" not in request.metadata["agent_allowed_tools"]


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
