from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
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
