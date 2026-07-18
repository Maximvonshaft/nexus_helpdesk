from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
from app.services.provider_runtime.router import (
    _bounded_provider_error_code,
    _bounded_provider_summary,
)
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
import app.services.provider_runtime.webchat_runtime_dispatcher as dispatcher


class _DummySession:
    def close(self) -> None:
        return None


class _AuditSession(_DummySession):
    def __init__(self, row=None, *, fail: bool = False):
        self.row = row
        self.fail = fail
        self.rollbacks = 0
        self.last_params = None

    def execute(self, statement, params=None):
        self.last_params = params
        if self.fail:
            raise RuntimeError("database unavailable")
        result = Mock()
        result.first.return_value = self.row
        return result

    def rollback(self):
        self.rollbacks += 1


def _provider_request() -> ProviderRequest:
    return ProviderRequest(
        request_id="request-1",
        tenant_id="tenant-1",
        tenant_key="tenant-1",
        channel_key="webchat",
        session_id="session-1",
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus.webchat_runtime_reply",
        timeout_ms=1000,
    )


def test_provider_error_codes_collapse_to_fixed_categories():
    assert _bounded_provider_error_code("private_ai_runtime_timeout") == "provider_timeout"
    assert _bounded_provider_error_code("private_ai_runtime_http_503") == "provider_http_error"
    assert _bounded_provider_error_code("private_ai_runtime_network_error") == "provider_network_error"
    assert _bounded_provider_error_code("private_ai_runtime_token_missing") == "provider_configuration_error"
    assert _bounded_provider_error_code("private_ai_runtime_bad_response") == "provider_output_invalid"
    assert _bounded_provider_error_code("customer supplied arbitrary text") == "provider_call_failed"


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
            "error_code": "private_ai_runtime_http_500",
            "prompt": "secret customer prompt",
            "raw_payload": {"customer_reply": "secret"},
        }
    )

    assert summary == {
        "provider": "private_ai_runtime",
        "endpoint_path": "/api/chat",
        "model": "qwen2.5:3b",
        "prompt_chars": 512,
        "token_file_configured": True,
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
        },
    }
    assert "must-not-cross" not in str(summary)
    assert "secret" not in str(summary)


def test_authoritative_audit_requires_exact_request_and_provider():
    db = _AuditSession(row=(1,))
    request = _provider_request()

    assert dispatcher._authoritative_provider_audit_exists(
        db,
        request=request,
        provider="private_ai_runtime",
    ) is True
    assert db.last_params == {
        "request_id": "request-1",
        "tenant_id": "tenant-1",
        "channel_key": "webchat",
        "session_id": "session-1",
        "provider": "private_ai_runtime",
    }


def test_authoritative_audit_query_failure_is_fail_closed():
    db = _AuditSession(fail=True)

    assert dispatcher._authoritative_provider_audit_exists(
        db,
        request=_provider_request(),
        provider="private_ai_runtime",
    ) is False
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_successful_provider_result_without_durable_audit_cannot_reply(monkeypatch):
    monkeypatch.setattr(dispatcher, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        dispatcher,
        "build_webchat_runtime_context",
        lambda *args, **kwargs: {
            "context_version": "nexus.webchat_runtime_context",
            "knowledge_context": {
                "retrieval": "unavailable",
                "locked_facts": [],
                "hits": [],
            },
        },
    )
    route = AsyncMock(
        return_value=ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            elapsed_ms=20,
            structured_output={
                "customer_reply": "must not become authoritative",
                "language": "en",
                "intent": "greeting",
                "handoff_required": False,
                "ticket_should_create": False,
            },
            raw_payload_safe_summary={
                "traffic": {
                    "path": "canary_authoritative",
                    "authoritative": True,
                    "execute_candidate": True,
                }
            },
        )
    )
    monkeypatch.setattr(dispatcher.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        dispatcher,
        "_authoritative_provider_audit_exists",
        lambda *args, **kwargs: False,
    )

    result = await dispatcher.dispatch_webchat_runtime_reply(
        request=RuntimeAIProviderRequest(
            tenant_key="tenant-1",
            channel_key="webchat",
            session_id="session-1",
            request_id="request-1",
            body="hello",
        )
    )

    assert result.ok is False
    assert result.ai_generated is False
    assert result.reply is None
    assert result.error_code == "provider_runtime_audit_unavailable"
    assert result.raw_payload_safe_summary["authoritative_audit"] == "unavailable"
    route.assert_awaited_once()
