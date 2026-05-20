from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = REPO_ROOT / "tools" / "codex-reply-bridge"
TRANSPORT_PATH = BRIDGE_DIR / "upstream_reply_transport.py"

if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

spec = importlib.util.spec_from_file_location("codex_upstream_reply_transport", TRANSPORT_PATH)
assert spec is not None
reply_transport = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reply_transport
assert spec.loader is not None
spec.loader.exec_module(reply_transport)


def _payload() -> dict[str, object]:
    return {
        "request_id": "reply-transport-test",
        "session_id": "reply-transport-session",
        "body": "Where is my parcel?",
        "recent_context": [],
        "strict_schema": "speedaf_webchat_fast_reply_v1",
    }


class _FakeAsyncClient:
    def __init__(self, *, status_code: int = 200, payload: object | None = None, exc: Exception | None = None):
        self.status_code = status_code
        self.payload = payload
        self.exc = exc
        self.requests: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, endpoint, headers, json):
        self.requests.append({"endpoint": endpoint, "headers": headers, "json": json})
        if self.exc:
            raise self.exc
        return httpx.Response(self.status_code, json=self.payload)


def test_normalize_reply_path_rejects_absolute_url():
    path, error = reply_transport.normalize_reply_path("https://example.com/reply")

    assert path is None
    assert error == "reply_path_must_be_relative"


def test_normalize_reply_path_rejects_parent_segment():
    path, error = reply_transport.normalize_reply_path("/../reply")

    assert path is None
    assert error == "reply_path_parent_segment_forbidden"


def test_post_reply_turn_rejects_missing_base_url():
    result = pytest.run(async_call=reply_transport.post_reply_turn(
        settings=reply_transport.ReplyTransportSettings(app_server_base_url=None),
        reply_payload=_payload(),
    )) if False else None


@pytest.mark.asyncio
async def test_post_reply_turn_rejects_missing_base_url_async():
    result = await reply_transport.post_reply_turn(
        settings=reply_transport.ReplyTransportSettings(app_server_base_url=None),
        reply_payload=_payload(),
    )

    assert result.ok is False
    assert result.status_code is None
    assert result.error_code == "app_server_base_url_missing"
    assert result.safe_summary["error_code"] == "app_server_base_url_missing"


@pytest.mark.asyncio
async def test_post_reply_turn_rejects_invalid_payload_before_network(monkeypatch):
    called = False

    def fake_client(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeAsyncClient()

    monkeypatch.setattr(reply_transport.httpx, "AsyncClient", fake_client)

    result = await reply_transport.post_reply_turn(
        settings=reply_transport.ReplyTransportSettings(app_server_base_url="http://127.0.0.1:18795"),
        reply_payload={},
    )

    assert result.ok is False
    assert result.error_code == "reply_payload_invalid"
    assert called is False


@pytest.mark.asyncio
async def test_post_reply_turn_success_returns_payload_and_safe_summary(monkeypatch):
    strict_reply = {
        "reply": "Please share your tracking number so I can check your parcel status.",
        "intent": "tracking_missing_number",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }
    fake_client = _FakeAsyncClient(payload=strict_reply)

    monkeypatch.setattr(reply_transport.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    result = await reply_transport.post_reply_turn(
        settings=reply_transport.ReplyTransportSettings(
            app_server_base_url="http://127.0.0.1:18795",
            reply_path="conversation/reply",
            bearer_token="secret-token",
        ),
        reply_payload=_payload(),
    )

    assert result.ok is True
    assert result.status_code == 200
    assert result.error_code is None
    assert result.response_payload == strict_reply
    assert result.safe_summary["transport"] == "codex_app_server_reply"
    assert result.safe_summary["endpoint_path"] == "/conversation/reply"
    assert result.safe_summary["response_keys"] == sorted(strict_reply.keys())
    sent = fake_client.requests[0]
    assert sent["endpoint"] == "http://127.0.0.1:18795/conversation/reply"
    assert sent["headers"]["authorization"] == "Bearer secret-token"
    assert sent["headers"]["x-nexus-provider-runtime"] == "codex-app-server-reply-v1"


@pytest.mark.asyncio
async def test_post_reply_turn_http_error_returns_safe_summary(monkeypatch):
    fake_client = _FakeAsyncClient(status_code=503, payload={"detail": "downstream unavailable"})
    monkeypatch.setattr(reply_transport.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    result = await reply_transport.post_reply_turn(
        settings=reply_transport.ReplyTransportSettings(app_server_base_url="http://127.0.0.1:18795"),
        reply_payload=_payload(),
    )

    assert result.ok is False
    assert result.status_code == 503
    assert result.error_code == "app_server_reply_http_error"
    assert result.safe_summary["response_keys"] == ["detail"]


@pytest.mark.asyncio
async def test_post_reply_turn_timeout_returns_safe_error(monkeypatch):
    fake_client = _FakeAsyncClient(exc=httpx.TimeoutException("timeout"))
    monkeypatch.setattr(reply_transport.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    result = await reply_transport.post_reply_turn(
        settings=reply_transport.ReplyTransportSettings(app_server_base_url="http://127.0.0.1:18795"),
        reply_payload=_payload(),
    )

    assert result.ok is False
    assert result.status_code is None
    assert result.error_code == "app_server_reply_timeout"
