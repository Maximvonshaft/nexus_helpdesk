from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.agent_runtime.runtime as agent_runtime
from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
from app.services.provider_runtime.router import (
    _bounded_provider_error_code,
    _bounded_provider_summary,
)
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class _AuditSession:
    def __init__(self, row=None, *, fail: bool = False):
        self.row = row
        self.fail = fail
        self.rollbacks = 0
        self.last_params = None

    def execute(self, statement, params=None):
        del statement
        self.last_params = params
        if self.fail:
            raise RuntimeError("database unavailable")
        result = Mock()
        result.first.return_value = self.row
        return result

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


def _provider_request() -> ProviderRequest:
    return ProviderRequest(
        request_id="request-1:round:0",
        tenant_id="tenant-1",
        tenant_key="tenant-1",
        channel_key="webchat",
        session_id="session-1",
        scenario="agent_turn",
        body="hello",
        output_contract="nexus.agent_turn.v1",
        timeout_ms=1000,
    )


def _bind_release(monkeypatch) -> None:
    snapshot = {
        "source": "deployment",
        "tenant_key": "tenant-1",
        "release": {
            "id": 1,
            "version": 1,
            "manifest_sha256": "a" * 64,
        },
        "deployment": {"id": 1, "environment": "production"},
        "manifest": {"integrations": [], "knowledge": []},
        "resolved": {"allowed_tools": []},
    }
    resolved = SimpleNamespace(
        snapshot=snapshot,
        digest="b" * 64,
        deployment=SimpleNamespace(id=1),
        release=SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        agent_runtime,
        "resolve_agent_release",
        lambda *_args, **_kwargs: resolved,
    )
    monkeypatch.setattr(
        agent_runtime,
        "record_run_snapshot",
        lambda *_args, **_kwargs: None,
    )
    run = SimpleNamespace(
        id=1,
        trace_id="trace-1",
        tenant_key="tenant-1",
        session_id="session-1",
        request_id="request-1",
        release_id=1,
        status="running",
        final_action=None,
        error_code=None,
    )
    monkeypatch.setattr(agent_runtime, "start_agent_run", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(agent_runtime, "bind_agent_run_release", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent_runtime, "append_agent_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent_runtime, "finish_agent_run", lambda *_args, **_kwargs: run)


def test_provider_error_codes_are_bounded_categories():
    assert _bounded_provider_error_code("private_ai_runtime_timeout") == "provider_timeout"
    assert _bounded_provider_error_code("customer supplied arbitrary text") == "provider_call_failed"
    assert _bounded_provider_error_code(None) == "provider_call_failed"


def test_provider_summary_keeps_only_bounded_structural_diagnostics():
    summary = _bounded_provider_summary(
        {
            "provider": "private_ai_runtime",
            "endpoint_path": "/api/chat",
            "model": "qwen2.5:3b",
            "prompt_chars": 512,
            "token_file_configured": True,
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "customer_text": "must-not-cross",
            },
            "reason": "upstream returned customer-controlled text",
            "raw_payload": {"customer_reply": "secret"},
        }
    )
    assert summary["provider"] == "private_ai_runtime"
    assert summary["endpoint_path"] == "/api/chat"
    assert summary["prompt_chars"] == 512
    assert summary["usage"]["prompt_tokens"] == 12
    assert "must-not-cross" not in str(summary)
    assert "secret" not in str(summary)


def test_authoritative_audit_requires_exact_round_request_and_provider():
    db = _AuditSession(row=(1,))
    request = _provider_request()
    assert agent_runtime._authoritative_provider_audit_exists(
        db,
        request=request,
        provider="private_ai_runtime",
    ) is True
    assert db.last_params == {
        "request_id": "request-1:round:0",
        "tenant_id": "tenant-1",
        "channel_key": "webchat",
        "session_id": "session-1",
        "provider": "private_ai_runtime",
    }


def test_authoritative_audit_query_failure_is_fail_closed():
    db = _AuditSession(fail=True)
    assert agent_runtime._authoritative_provider_audit_exists(
        db,
        request=_provider_request(),
        provider="private_ai_runtime",
    ) is False
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_successful_provider_result_without_durable_audit_becomes_visible_fallback(
    monkeypatch,
):
    route = AsyncMock(
        return_value=ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            elapsed_ms=20,
            structured_output={
                "customer_reply": "must not become authoritative",
                "intent": "general_support",
                "next_action": "reply",
                "handoff_required": False,
                "tool_calls": [],
            },
            raw_payload_safe_summary={
                "traffic": {"path": "canary_authoritative"}
            },
        )
    )
    _bind_release(monkeypatch)
    monkeypatch.setattr(agent_runtime.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        agent_runtime,
        "_authoritative_provider_audit_exists",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        agent_runtime,
        "_runtime_policy",
        lambda *args, **kwargs: {
            "max_tool_rounds": 3,
            "allow_high_risk_writes": False,
            "allowed_tools": [],
            "provider_timeout_ms": 15000,
            "enabled": True,
        },
    )
    monkeypatch.setattr(
        agent_runtime,
        "prompt_playbook_catalog",
        lambda *args, **kwargs: [],
    )

    result = await agent_runtime.run_agent_with_db(
        _AuditSession(),
        request=RuntimeAIProviderRequest(
            tenant_key="tenant-1",
            channel_key="webchat",
            session_id="session-1",
            request_id="request-1",
            body="hello",
            language="en",
            metadata={"agent_allowed_tools": []},
        ),
        started=time.monotonic(),
    )
    assert result.ok is True
    assert result.ai_generated is False
    assert result.reply
    assert result.error_code == "provider_runtime_audit_unavailable"
    assert result.raw_payload_safe_summary["error_code"] == (
        "provider_runtime_audit_unavailable"
    )
    assert result.raw_payload_safe_summary["rounds"][0]["error_code"] == (
        "provider_runtime_audit_unavailable"
    )
    route.assert_awaited_once()
