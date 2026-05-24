from __future__ import annotations

import importlib.util
import json
import socket
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[2]


def _load_upstream_module(
    monkeypatch,
    tmp_path,
    *,
    private_url: str = "",
    backend: str = "codex_private_runtime",
    readyz_timeout: str = "1",
):
    monkeypatch.setenv("CODEX_APP_SERVER_PRIVATE_REPLY_URL", private_url)
    monkeypatch.setenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", backend)
    monkeypatch.setenv("CODEX_APP_SERVER_PRIVATE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS", readyz_timeout)
    spec = importlib.util.spec_from_file_location(
        f"codex_app_server_private_upstream_proxy_test_{id(tmp_path)}",
        ROOT / "deploy" / "codex_app_server_private_upstream_proxy.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_json(url: str) -> tuple[int, dict]:
    try:
        with request.urlopen(url, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_json(url: str, payload: dict, *, token: str | None = "oauth-access") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _strict_reply(reply: str = "private nonce") -> dict:
    return {
        "reply": reply,
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def test_private_upstream_healthz_is_liveness_only(monkeypatch, tmp_path):
    upstream = _load_upstream_module(monkeypatch, tmp_path, private_url="")
    server = upstream.ThreadingHTTPServer(("127.0.0.1", 0), upstream.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _read_json(f"http://127.0.0.1:{server.server_address[1]}/healthz")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert payload["ok"] is True


def test_private_upstream_readyz_fails_when_private_endpoint_missing(monkeypatch, tmp_path):
    upstream = _load_upstream_module(monkeypatch, tmp_path, private_url="")

    payload = upstream.readiness_payload()

    assert payload["ok"] is False
    assert payload["reason"] == "codex_private_reply_endpoint_not_configured"


def test_private_upstream_readyz_ok_when_private_endpoint_reachable(monkeypatch, tmp_path):
    class PrivateHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    private = ThreadingHTTPServer(("127.0.0.1", 0), PrivateHandler)
    private_thread = threading.Thread(target=private.serve_forever, daemon=True)
    private_thread.start()
    try:
        upstream = _load_upstream_module(monkeypatch, tmp_path, private_url=f"http://127.0.0.1:{private.server_address[1]}/reply")
        payload = upstream.readiness_payload()
    finally:
        private.shutdown()
        private.server_close()
        private_thread.join(timeout=2)

    assert payload["ok"] is True
    assert payload["private_reply_configured"] is True
    assert payload["private_reply_reachable"] is True


def test_private_upstream_honors_30_second_readyz_timeout(monkeypatch, tmp_path):
    upstream = _load_upstream_module(
        monkeypatch,
        tmp_path,
        private_url="http://codex-private-reply-engine:18796/reply",
        readyz_timeout="30",
    )
    observed: dict[str, float] = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout):
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(upstream.request, "urlopen", fake_urlopen)

    assert upstream.private_ready() is True
    assert observed["timeout"] == 30


def test_private_upstream_slow_readyz_within_timeout_is_reachable(monkeypatch, tmp_path):
    class PrivateHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            time.sleep(0.2)
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    private = ThreadingHTTPServer(("127.0.0.1", 0), PrivateHandler)
    private_thread = threading.Thread(target=private.serve_forever, daemon=True)
    private_thread.start()
    try:
        upstream = _load_upstream_module(
            monkeypatch,
            tmp_path,
            private_url=f"http://127.0.0.1:{private.server_address[1]}/reply",
            readyz_timeout="1",
        )
        payload = upstream.readiness_payload()
    finally:
        private.shutdown()
        private.server_close()
        private_thread.join(timeout=2)

    assert payload["ok"] is True
    assert payload["private_reply_reachable"] is True


def test_private_upstream_readyz_timeout_is_not_reachable(monkeypatch, tmp_path):
    upstream = _load_upstream_module(
        monkeypatch,
        tmp_path,
        private_url="http://codex-private-reply-engine:18796/reply",
        readyz_timeout="30",
    )

    def fake_urlopen(req, timeout):
        raise socket.timeout("timed out")

    monkeypatch.setattr(upstream.request, "urlopen", fake_urlopen)
    payload = upstream.readiness_payload()

    assert payload["ok"] is False
    assert payload["private_reply_reachable"] is False
    assert payload["reason"] == "codex_private_reply_endpoint_unreachable"


def test_private_upstream_reply_forwards_contract_and_returns_strict_json(monkeypatch, tmp_path):
    captured: dict = {}

    class PrivateHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            captured["authorization"] = self.headers.get("Authorization")
            captured["payload"] = json.loads(self.rfile.read(length).decode("utf-8"))
            raw = json.dumps(_strict_reply("strict private reply")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    private = ThreadingHTTPServer(("127.0.0.1", 0), PrivateHandler)
    private_thread = threading.Thread(target=private.serve_forever, daemon=True)
    private_thread.start()
    upstream = _load_upstream_module(monkeypatch, tmp_path, private_url=f"http://127.0.0.1:{private.server_address[1]}/reply")
    server = upstream.ThreadingHTTPServer(("127.0.0.1", 0), upstream.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(
            f"http://127.0.0.1:{server.server_address[1]}/reply",
            {
                "body": "hello",
                "messages": [{"role": "user", "content": "hello"}],
                "contract": "speedaf_webchat_fast_reply_v1",
                "tracking_fact_summary": "masked fact",
                "tracking_fact_evidence_present": True,
                "chatgptAccountId": "acct-1",
                "chatgptPlanType": "plus",
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        private.shutdown()
        private.server_close()
        private_thread.join(timeout=2)

    assert status == 200
    assert payload == _strict_reply("strict private reply")
    assert captured["authorization"].split(" ", 1) == ["Bearer", "oauth-access"]
    assert captured["payload"]["body"] == "hello"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["payload"]["contract"] == "speedaf_webchat_fast_reply_v1"
    assert captured["payload"]["chatgptAccountId"] == "acct-1"
    assert "oauth-access" not in json.dumps(payload)


def test_private_upstream_reply_rejects_invalid_private_response(monkeypatch, tmp_path):
    class PrivateHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            raw = json.dumps({"reply": "not strict"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    private = ThreadingHTTPServer(("127.0.0.1", 0), PrivateHandler)
    private_thread = threading.Thread(target=private.serve_forever, daemon=True)
    private_thread.start()
    upstream = _load_upstream_module(monkeypatch, tmp_path, private_url=f"http://127.0.0.1:{private.server_address[1]}/reply")
    server = upstream.ThreadingHTTPServer(("127.0.0.1", 0), upstream.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", {"body": "hello"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        private.shutdown()
        private.server_close()
        private_thread.join(timeout=2)

    assert status == 502
    assert payload["error"] == "private_reply_missing_required_fields"
    assert "oauth-access" not in json.dumps(payload)


def test_private_upstream_reply_rejects_missing_oauth_bearer(monkeypatch, tmp_path):
    upstream = _load_upstream_module(monkeypatch, tmp_path, private_url="")
    server = upstream.ThreadingHTTPServer(("127.0.0.1", 0), upstream.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", {"body": "hello"}, token=None)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 401
    assert payload == {"ok": False, "error": "oauth_bearer_required"}
