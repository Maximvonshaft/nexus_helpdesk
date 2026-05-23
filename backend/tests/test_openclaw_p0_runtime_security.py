from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from types import SimpleNamespace

from app.enums import MessageStatus
from app.services import openclaw_bridge
from app.services import openclaw_p0_runtime_security as hardening


def test_redact_route_context_masks_sensitive_fields() -> None:
    route = {
        "channel": "whatsapp",
        "target": "+15550001234",
        "recipient": "+15550005678",
        "session_key": "session-secret-value",
        "idempotency_key": "idempotency-secret-value",
    }

    redacted = hardening.redact_route_context(route)
    rendered = str(redacted)

    assert "+15550001234" not in rendered
    assert "+15550005678" not in rendered
    assert "session-secret-value" not in rendered
    assert "idempotency-secret-value" not in rendered
    assert redacted["session_key"].startswith("sha256:")
    assert redacted["idempotency_key"].startswith("sha256:")


def test_remote_attachment_fetch_rejects_redirects(monkeypatch) -> None:
    hardening.apply_openclaw_p0_runtime_security_patch()

    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_attachment_url_fetch_enabled", True)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_attachment_allowed_hosts", ["cdn.example.com"])
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_attachment_allowed_mime_types", ["text/plain"])
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_attachment_fetch_timeout_seconds", 1)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_attachment_max_download_bytes", 1024)
    monkeypatch.setattr(openclaw_bridge, "_resolved_host_is_public", lambda hostname: True)

    class RedirectingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "http://127.0.0.1/internal"},
                None,
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: RedirectingOpener())

    assert openclaw_bridge._try_fetch_remote_attachment(
        "https://cdn.example.com/file.txt",
        {"contentType": "text/plain", "filename": "file.txt"},
    ) == (None, None, None)


def test_cli_fallback_does_not_log_body_or_full_command(monkeypatch) -> None:
    hardening.apply_openclaw_p0_runtime_security_patch()

    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bin", "openclaw")
    records: list[dict] = []

    class CaptureLogger:
        def warning(self, message, *args, **kwargs):
            records.append({"level": "warning", "message": message, "extra": kwargs.get("extra")})

        def info(self, message, *args, **kwargs):
            records.append({"level": "info", "message": message, "extra": kwargs.get("extra")})

    monkeypatch.setattr(openclaw_bridge, "LOGGER", CaptureLogger())
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    status_value, provider_status, sent_at = openclaw_bridge.dispatch_via_openclaw_cli(
        channel="whatsapp",
        target="+15550001234",
        body="secret customer message body",
        account_id="acct-1",
        thread_id="thread-1",
    )

    rendered = str(records)
    assert status_value == MessageStatus.sent
    assert provider_status == "sent_via_openclaw_cli_fallback"
    assert sent_at is not None
    assert "secret customer message body" not in rendered
    assert "command" not in rendered
    assert "+15550001234" not in rendered


def test_mcp_fallback_processes_events_before_client_context_closes(monkeypatch) -> None:
    hardening.apply_openclaw_p0_runtime_security_patch()

    class FakeCursorColumn:
        def __eq__(self, other):
            return True

    class FakeCursorModel:
        source = FakeCursorColumn()

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery()

        def flush(self):
            pass

    class FakeClient:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.closed = True

        def events_wait(self, cursor, timeout_seconds):
            return {
                "events": [
                    {
                        "type": "message",
                        "sessionKey": "session-1",
                        "route": {"recipient": "+15550001234"},
                        "cursor": 7,
                    }
                ],
                "nextCursor": 7,
            }

        def events_poll(self, cursor):
            raise AssertionError("events_poll should not be needed in this regression path")

    fake_client = FakeClient()
    seen_clients: list[FakeClient] = []

    def fake_process(db, *, event, source, client=None):
        assert client is fake_client
        assert client.closed is False
        seen_clients.append(client)
        return True

    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_bridge_enabled", False)
    monkeypatch.setattr(openclaw_bridge.settings, "openclaw_sync_poll_timeout_seconds", 1)
    monkeypatch.setattr(openclaw_bridge, "OpenClawSyncCursor", FakeCursorModel)
    monkeypatch.setattr(openclaw_bridge, "OpenClawMCPClient", lambda: fake_client)
    monkeypatch.setattr(openclaw_bridge, "_local_mcp_fallback_allowed", lambda: True)
    monkeypatch.setattr(openclaw_bridge, "process_openclaw_inbound_event", fake_process)
    monkeypatch.setattr(openclaw_bridge, "upsert_openclaw_sync_cursor", lambda db, source, cursor_value: None)

    assert openclaw_bridge.consume_openclaw_events_once(FakeDB(), timeout_seconds=1) == 1
    assert seen_clients == [fake_client]
    assert fake_client.closed is True
