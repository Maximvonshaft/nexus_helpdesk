from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = REPO_ROOT / "tools" / "codex-reply-bridge"
DISCOVERY_PATH = TOOL_DIR / "reply_protocol_discovery.py"

if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

spec = importlib.util.spec_from_file_location("codex_reply_protocol_discovery", DISCOVERY_PATH)
assert spec is not None
discovery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = discovery
assert spec.loader is not None
spec.loader.exec_module(discovery)


def test_parse_candidate_paths_normalizes_and_dedupes():
    assert discovery.parse_candidate_paths("reply,/turn, reply ,,chat") == ["/reply", "/turn", "/chat"]


def test_synthetic_post_payload_is_marked_as_probe():
    payload = discovery.synthetic_post_payload("/reply")

    assert payload["probe"] is True
    assert payload["request_id"] == "protocol-discovery-probe"
    assert "Synthetic protocol discovery probe" in payload["body"]


def test_discover_rejects_missing_base_url():
    result = asyncio.run(
        discovery.discover_reply_protocol(
            discovery.ProbeSettings(base_url=None, candidate_paths=["/healthz"]),
        )
    )

    assert result.ok is False
    assert result.base_url_accepted is False
    assert result.error_code == "app_server_base_url_missing"
    assert result.results == []


def test_discover_rejects_public_http_url():
    result = asyncio.run(
        discovery.discover_reply_protocol(
            discovery.ProbeSettings(base_url="http://example.com", candidate_paths=["/healthz"]),
        )
    )

    assert result.ok is False
    assert result.error_code == "app_server_http_requires_loopback"


def test_discover_default_methods_do_not_post(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object] | None = None):
            self.status_code = status_code
            self._payload = payload or {"ok": True}
            self.headers = httpx.Headers({"content-type": "application/json", "allow": "GET, OPTIONS"})
            self.content = b'{"ok":true}'

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, endpoint, headers):
            calls.append((method, endpoint, None))
            return FakeResponse(200, {"ok": True, "method": method})

        async def post(self, endpoint, headers, json):
            calls.append(("POST", endpoint, json))
            return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        discovery.discover_reply_protocol(
            discovery.ProbeSettings(base_url="http://127.0.0.1:18795", candidate_paths=["/healthz"]),
        )
    )
    safe = discovery.result_to_safe_dict(result)

    assert result.ok is True
    assert [call[0] for call in calls] == ["OPTIONS", "GET"]
    assert safe["boundary"]["post_probe_enabled"] is False
    assert safe["boundary"]["credential_material_sent"] is False
    assert safe["boundary"]["customer_message_sent"] is False
    assert safe["results"][0]["response_keys"] == ["method", "ok"]


def test_discover_post_probe_requires_explicit_enable(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    class FakeResponse:
        status_code = 200
        headers = httpx.Headers({"content-type": "application/json"})
        content = b'{"ok":true,"reply":"ignored"}'

        def json(self):
            return {"ok": True, "reply": "ignored"}

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, endpoint, headers):
            calls.append((method, endpoint, None))
            return FakeResponse()

        async def post(self, endpoint, headers, json):
            calls.append(("POST", endpoint, json))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        discovery.discover_reply_protocol(
            discovery.ProbeSettings(
                base_url="http://127.0.0.1:18795",
                candidate_paths=["/reply"],
                allow_post_probe=True,
            ),
        )
    )
    safe = discovery.result_to_safe_dict(result)

    assert [call[0] for call in calls] == ["OPTIONS", "GET", "POST"]
    assert calls[-1][2]["probe"] is True  # type: ignore[index]
    assert calls[-1][2]["request_id"] == "protocol-discovery-probe"  # type: ignore[index]
    assert safe["boundary"]["post_probe_enabled"] is True
    assert safe["results"][-1]["response_keys"] == ["ok", "reply"]
    assert "Synthetic protocol discovery probe" not in str(safe)


def test_result_summary_does_not_include_response_body_or_probe_payload(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = httpx.Headers({"content-type": "application/json"})
        content = b'{"ok":true,"text":"sensitive response body should not be copied"}'

        def json(self):
            return {"ok": True, "text": "sensitive response body should not be copied"}

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, endpoint, headers):
            return FakeResponse()

        async def post(self, endpoint, headers, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        discovery.discover_reply_protocol(
            discovery.ProbeSettings(base_url="http://127.0.0.1:18795", candidate_paths=["/reply"], allow_post_probe=True),
        )
    )
    safe = discovery.result_to_safe_dict(result)

    assert "sensitive response body should not be copied" not in str(safe)
    assert safe["results"][-1]["response_keys"] == ["ok", "text"]
}
