from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.enums import MessageStatus, SourceChannel
from app.services import message_dispatch


class ProviderCalled(RuntimeError):
    pass


def _message(channel=SourceChannel.whatsapp, status=MessageStatus.processing):
    return SimpleNamespace(
        id=1,
        ticket_id=101,
        channel=channel,
        status=status,
        body="hello",
        provider_status="queued",
        error_message=None,
        failure_code=None,
        failure_reason=None,
        retry_count=0,
        max_retries=3,
        last_attempt_at=None,
        next_retry_at=None,
        locked_at=None,
        locked_by="worker-test",
        created_by=None,
        provider_message_id=None,
        sent_at=None,
        ticket=None,
    )


def _provider_must_not_run(*args, **kwargs):
    raise ProviderCalled("provider path must not run")


def test_ensure_external_dispatch_allowed_fails_when_dispatch_disabled(monkeypatch):
    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", False)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "openclaw")
    with pytest.raises(RuntimeError, match="ENABLE_OUTBOUND_DISPATCH=false"):
        message_dispatch.ensure_external_dispatch_allowed()


def test_ensure_external_dispatch_allowed_fails_when_provider_disabled(monkeypatch):
    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "disabled")
    with pytest.raises(RuntimeError, match="OUTBOUND_PROVIDER=disabled"):
        message_dispatch.ensure_external_dispatch_allowed()


def test_ensure_external_dispatch_allowed_fails_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "unknown")
    with pytest.raises(RuntimeError, match="Unsupported OUTBOUND_PROVIDER"):
        message_dispatch.ensure_external_dispatch_allowed()


def test_process_external_message_provider_disabled_never_calls_provider(monkeypatch):
    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "disabled")
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_bridge", _provider_must_not_run)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_mcp", _provider_must_not_run)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_cli", _provider_must_not_run)

    row = _message(SourceChannel.whatsapp)
    processed = message_dispatch.process_outbound_message(SimpleNamespace(), row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "outbound_provider_disabled"


def test_process_webchat_pending_row_never_calls_provider(monkeypatch):
    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "openclaw")
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_bridge", _provider_must_not_run)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_mcp", _provider_must_not_run)
    monkeypatch.setattr(message_dispatch, "dispatch_via_openclaw_cli", _provider_must_not_run)

    row = _message(SourceChannel.web_chat)
    processed = message_dispatch.process_outbound_message(SimpleNamespace(), row)

    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "non_external_outbound_not_dispatchable"
