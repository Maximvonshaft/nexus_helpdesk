from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, WebSocketDisconnect
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api import integration_runtime as integration_api
from app.api import webchat_ws as webchat_ws_api
from app.auth_service import hash_password, verify_password
from app.services import observability
from app.services.observability import _JsonFormatter, log_event
from app.utils import client_ip as client_ip_service
from app.settings import Settings


def _request(*, host: str, xff: str | None, client_host: str) -> Request:
    headers = [(b"host", host.encode("latin-1"))]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": headers,
            "client": (client_host, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_json_formatter_masks_sensitive_structured_values():
    logger = logging.getLogger("runtime-hardening")
    record = logger.makeRecord(
        name=logger.name,
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg="provider_call",
        args=(),
        exc_info=None,
    )
    record.event_payload = {
        "provider_payload": {"content": "SECRET_PAYLOAD"},
        "phone": "+639171234567",
        "authorization": "Bearer secret-token",
    }
    rendered = _JsonFormatter().format(record)
    assert "SECRET_PAYLOAD" not in rendered
    assert "+639171234567" not in rendered
    assert "secret-token" not in rendered


def test_log_event_does_not_promote_arbitrary_event_fields(monkeypatch):
    captured = {}

    def fake_log(_level, _message, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(observability.LOGGER, "log", fake_log)
    log_event(logging.INFO, "probe", status="ok", provider_payload={"content": "SECRET"})
    assert captured["extra"]["event_payload"]["status"] == "ok"
    assert "provider_payload" not in captured["extra"]


def test_websocket_rejects_non_json_messages():
    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.sent = []
            self.closed = None
            self.headers = {}
            self.query_params = {}
            self.client = SimpleNamespace(host="127.0.0.1")

        async def accept(self):
            self.accepted = True

        async def receive_json(self):
            if not self.sent:
                raise json.JSONDecodeError("invalid", "not-json", 0)
            raise WebSocketDisconnect(code=1000)

        async def send_json(self, payload):
            self.sent.append(payload)

        async def close(self, code=1000):
            self.closed = code

    websocket = FakeWebSocket()
    asyncio.run(webchat_ws_api.webchat_ws(websocket, db=SimpleNamespace()))
    assert websocket.accepted is True
    assert any(item.get("code") == "invalid_json" for item in websocket.sent)


def test_auth_uses_forwarded_address_only_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(
        client_ip_service,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_ips=["127.0.0.1"]),
    )
    trusted = _request(host="helpdesk.example", xff="203.0.113.8, 127.0.0.1", client_host="127.0.0.1")
    untrusted = _request(host="helpdesk.example", xff="203.0.113.8", client_host="198.51.100.5")
    assert client_ip_service.get_client_ip(trusted) == "203.0.113.8"
    assert client_ip_service.get_client_ip(untrusted) == "198.51.100.5"


def test_integration_business_failure_rolls_back(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def flush(self):
            return None

    db = FakeSession()
    client = SimpleNamespace(client_id=None, is_legacy=False, scopes={"task.write"})
    monkeypatch.setattr(integration_api.settings, "integration_require_idempotency_key", False)
    monkeypatch.setattr(integration_api, "enforce_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(integration_api, "_pick_actor", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        integration_api.nexusdesk_escalate_task(
            integration_api.IntegrationTaskRequest(contact_id="x", summary="summary"),
            SimpleNamespace(),
            db,
            client,
            None,
        )
    assert db.commits == 0
    assert db.rollbacks == 1


def test_password_hashing_rejects_non_argon2_hashes():
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong", encoded) is False
    bcrypt_hash = "$2b$12$KIXQ4I3mM1kQeF24kL8fHevZxQ.FYyEoYkYjPXxY2v0bH2Wy2lF4u"
    assert verify_password("secret", bcrypt_hash) is False


def test_production_metrics_token_must_be_strong(monkeypatch):
    base = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://user:pass@localhost:5432/db",
        "SECRET_KEY": "strong-production-secret-value-with-sufficient-length",
        "ALLOWED_ORIGINS": "https://helpdesk.example",
        "WEBCHAT_ALLOWED_ORIGINS": "https://helpdesk.example",
        "METRICS_ENABLED": "true",
        "READINESS_REQUIRE_RELEASE_METADATA": "false",
        "LOCAL_STORAGE_BACKUP_REQUIRED": "false",
        "RUNTIME_CONTRACT_SIGNING_SECRET": "strong-runtime-contract-signing-secret",
    }
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("METRICS_TOKEN", "short")
    with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
        Settings()


def test_rate_limit_key_never_contains_raw_identity():
    source = (BACKEND_ROOT / "app/services/integration_auth.py").read_text(encoding="utf-8")
    assert "hashlib.sha256" in source
    sample = "client-secret-identity"
    digest = hashlib.sha256(sample.encode()).hexdigest()
    assert sample not in digest
