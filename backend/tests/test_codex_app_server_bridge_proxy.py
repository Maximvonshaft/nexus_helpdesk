from __future__ import annotations

import importlib.util
import json
import socket
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REAL_UPSTREAM_URL = "http://codex-private-reply-engine:18796/reply"


def _load_bridge_module(
    monkeypatch,
    tmp_path,
    *,
    upstream_url: str = "http://127.0.0.1:18795/reply",
    token: str | None = "bridge-token",
    readyz_timeout: str = "1",
    backend_label: str | None = "codex_app_server",
):
    token_file = tmp_path / "codex_app_server_bridge_token"
    if token is not None:
        token_file.write_text(token, encoding="utf-8")
        monkeypatch.setenv("CODEX_APP_SERVER_TOKEN_FILE", str(token_file))
    else:
        monkeypatch.delenv("CODEX_APP_SERVER_TOKEN_FILE", raising=False)
        monkeypatch.setenv("TOKEN_FILE", str(tmp_path / "missing-token"))
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_MODE", "real")
    if upstream_url:
        monkeypatch.setenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", upstream_url)
    else:
        monkeypatch.delenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", raising=False)
    if backend_label is None:
        monkeypatch.delenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", raising=False)
    else:
        monkeypatch.setenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", backend_label)
    monkeypatch.setenv("CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS", readyz_timeout)
    spec = importlib.util.spec_from_file_location(
        f"codex_app_server_bridge_proxy_test_{id(tmp_path)}",
        ROOT / "deploy" / "codex_app_server_bridge_proxy.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module._LOGIN_STATE.update({
        "access_token": None,
        "chatgpt_account_id": None,
        "chatgpt_plan_type": None,
        "updated_at": None,
    })
    return module


@pytest.fixture()
def bridge_server(monkeypatch, tmp_path):
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="", backend_label=None)
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bridge, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _read_json(url: str) -> tuple[int, dict]:
    try:
        with request.urlopen(url, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_json(url: str, payload: dict, *, token: str | None = "bridge-token") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _strict_reply(reply: str = "nonce-from-upstream") -> dict:
    return {
        "reply": reply,
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def _reply_with_login(body: str = "hello") -> dict:
    return {
        "login": {
            "type": "chatgptAuthTokens",
            "accessToken": "oauth-access",
            "chatgptAccountId": "acct-1",
            "chatgptPlanType": "plus",
        },
        "body": body,
        "messages": [],
    }


def test_bridge_healthz_is_liveness_only(bridge_server):
    _bridge, base_url = bridge_server

    status, payload = _read_json(base_url + "/healthz")

    assert status == 200
    assert payload["ok"] is True
    assert payload["service"] == "nexus-codex-app-server-bridge-proxy"


def test_bridge_readyz_reports_real_upstream_status(bridge_server):
    _bridge, base_url = bridge_server

    status, payload = _read_json(base_url + "/readyz")

    assert status == 503
    assert payload["ok"] is False
    assert payload["reason"] == "codex_app_server_real_upstream_unreachable"
    assert payload["real_upstream_configured"] is True
    assert payload["real_upstream_url"] == DEFAULT_REAL_UPSTREAM_URL
    assert "bridge-token" not in json.dumps(payload)


def test_bridge_readyz_returns_200_when_upstream_configured_and_reachable(monkeypatch, tmp_path):
    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    try:
        bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
        payload = bridge.readiness_payload()
    finally:
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert payload["ok"] is True
    assert payload["real_upstream_configured"] is True
    assert payload["real_upstream_reachable"] is True
    assert payload["reason"] is None


def test_bridge_honors_30_second_readyz_timeout(monkeypatch, tmp_path):
    bridge = _load_bridge_module(
        monkeypatch,
        tmp_path,
        upstream_url="http://codex-app-server-upstream:18795/reply",
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

    monkeypatch.setattr(bridge.request, "urlopen", fake_urlopen)

    assert bridge.upstream_ready() is True
    assert observed["timeout"] == 30


def test_bridge_slow_upstream_readyz_within_timeout_is_reachable(monkeypatch, tmp_path):
    class UpstreamHandler(BaseHTTPRequestHandler):
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

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    try:
        bridge = _load_bridge_module(
            monkeypatch,
            tmp_path,
            upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply",
            readyz_timeout="1",
        )
        payload = bridge.readiness_payload()
    finally:
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert payload["ok"] is True
    assert payload["real_upstream_reachable"] is True


def test_bridge_upstream_readyz_timeout_is_not_reachable(monkeypatch, tmp_path):
    bridge = _load_bridge_module(
        monkeypatch,
        tmp_path,
        upstream_url="http://codex-app-server-upstream:18795/reply",
        readyz_timeout="30",
    )

    def fake_urlopen(req, timeout):
        raise socket.timeout("timed out")

    monkeypatch.setattr(bridge.request, "urlopen", fake_urlopen)
    payload = bridge.readiness_payload()

    assert payload["ok"] is False
    assert payload["real_upstream_reachable"] is False
    assert payload["reason"] == "codex_app_server_real_upstream_unreachable"


def test_bridge_readyz_returns_503_when_upstream_configured_but_unreachable(monkeypatch, tmp_path):
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="http://127.0.0.1:9/reply")

    payload = bridge.readiness_payload()

    assert payload["ok"] is False
    assert payload["real_upstream_configured"] is True
    assert payload["real_upstream_reachable"] is False
    assert payload["reason"] == "codex_app_server_real_upstream_unreachable"


def test_bridge_readyz_fails_closed_without_real_upstream(monkeypatch, tmp_path):
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="", backend_label=None)

    payload = bridge.readiness_payload()

    assert payload["ok"] is False
    assert payload["reason"] == "codex_app_server_real_upstream_unreachable"
    assert payload["real_upstream_configured"] is True
    assert payload["real_upstream_url"] == DEFAULT_REAL_UPSTREAM_URL


def test_bridge_allows_container_compatible_bind_host(monkeypatch, tmp_path):
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="")
    monkeypatch.setattr(bridge, "BIND_HOST", "0.0.0.0")

    bridge.check_bind_host()


def test_bridge_reply_rejects_missing_bearer_token(bridge_server):
    _bridge, base_url = bridge_server

    status, payload = _post_json(base_url + "/reply", {"body": "hello"}, token=None)

    assert status == 401
    assert payload == {"ok": False, "error": "unauthorized"}


def test_bridge_reply_fails_closed_when_login_missing(monkeypatch, tmp_path):
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", {"body": "hello"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 502
    assert payload["error"] == "codex_login_required"
    assert "access_token" not in json.dumps(payload)


def test_bridge_reply_forwards_prompt_to_real_upstream(monkeypatch, tmp_path):
    captured: dict = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            captured["url"] = self.path
            captured["authorization"] = self.headers.get("Authorization")
            captured["payload"] = json.loads(self.rfile.read(length).decode("utf-8"))
            raw = json.dumps(_strict_reply()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        login_status, login_payload = _post_json(
            base_url + "/login",
            {"login": {"type": "chatgptAuthTokens", "accessToken": "oauth-access", "chatgptAccountId": "acct-1"}},
        )
        reply_status, reply_payload = _post_json(base_url + "/reply", _reply_with_login("Echo nonce-from-upstream"))
    finally:
        server.shutdown()
        server.server_close()
        bridge_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert login_status == 200
    assert login_payload == {"ok": True}
    assert reply_status == 200
    assert reply_payload["reply"] == "nonce-from-upstream"
    assert captured["url"] == "/reply"
    assert captured["authorization"].split(" ", 1) == ["Bearer", "oauth-access"]
    assert captured["payload"]["body"] == "Echo nonce-from-upstream"
    assert captured["payload"]["login"]["type"] == "chatgptAuthTokens"
    assert captured["payload"]["login"]["accessToken"] == "oauth-access"
    assert captured["payload"]["login"]["chatgptAccountId"] == "acct-1"
    rendered = json.dumps(reply_payload) + json.dumps(login_payload)
    assert "oauth-access" not in rendered
    assert "bridge-token" not in rendered


def test_bridge_reply_hot_path_does_not_probe_upstream_readyz(monkeypatch, tmp_path):
    captured: dict = {"get_called": False}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            captured["get_called"] = True
            self.send_response(500)
            self.end_headers()

        def do_POST(self) -> None:
            raw = json.dumps(_strict_reply("hot path reply")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", _reply_with_login("hello"))
    finally:
        server.shutdown()
        server.server_close()
        bridge_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert status == 200
    assert payload["reply"] == "hot path reply"
    assert captured["get_called"] is False


def test_bridge_reply_parallel_requests_do_not_share_oauth_state(monkeypatch, tmp_path):
    captured: list[dict] = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            captured.append({"authorization": self.headers.get("Authorization"), "body": payload.get("body")})
            raw = json.dumps(_strict_reply(payload.get("body") or "ok")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()

    def post_with_token(name: str, oauth_token: str) -> tuple[int, dict]:
        payload = _reply_with_login(name)
        payload["login"]["accessToken"] = oauth_token
        return _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", payload)

    try:
        results: list[tuple[int, dict]] = []
        threads = [
            threading.Thread(target=lambda: results.append(post_with_token("first", "oauth-one"))),
            threading.Thread(target=lambda: results.append(post_with_token("second", "oauth-two"))),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        bridge_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert sorted(status for status, _payload in results) == [200, 200]
    by_body = {item["body"]: item["authorization"] for item in captured}
    assert by_body["first"] == "Bearer oauth-one"
    assert by_body["second"] == "Bearer oauth-two"
    assert bridge._LOGIN_STATE["access_token"] is None


def test_bridge_routes_to_node_appserver_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_APP_SERVER_RUNTIME_BACKEND", "node_appserver")
    monkeypatch.delenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL_NODE", raising=False)
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="", backend_label=None)

    assert bridge.RUNTIME_BACKEND == "node_appserver"
    assert bridge.REAL_UPSTREAM_URL == "http://codex-appserver-runtime:18810/reply"
    assert bridge.EFFECTIVE_REPLY_GENERATION_BACKEND == "nexus_codex_appserver_runtime"


def test_bridge_routes_to_python_cli_pool_rollback(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_APP_SERVER_RUNTIME_BACKEND", "python_cli_pool")
    monkeypatch.delenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL_PYTHON", raising=False)
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="", backend_label=None)

    assert bridge.RUNTIME_BACKEND == "python_cli_pool"
    assert bridge.REAL_UPSTREAM_URL == DEFAULT_REAL_UPSTREAM_URL
    assert bridge.EFFECTIVE_REPLY_GENERATION_BACKEND == "python_cli_pool"


def test_bridge_expired_deadline_returns_timeout_before_upstream_call(monkeypatch, tmp_path):
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url="http://127.0.0.1:18795/reply")
    handler = type("Handler", (), {"headers": {"X-Nexus-Request-Deadline-Ms": str(int(time.time() * 1000) - 1)}})()

    def fail_urlopen(req, timeout):
        raise AssertionError("expired deadline must not call upstream")

    monkeypatch.setattr(bridge.request, "urlopen", fail_urlopen)

    with pytest.raises(TimeoutError):
        bridge.call_real_upstream(handler, _reply_with_login("hello"))


def test_bridge_reply_rejects_invalid_upstream_reply(monkeypatch, tmp_path):
    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_POST(self) -> None:
            raw = json.dumps({"reply": "missing strict fields"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        _post_json(base_url + "/login", {"login": {"type": "chatgptAuthTokens", "accessToken": "oauth-access"}})
        status, payload = _post_json(base_url + "/reply", _reply_with_login("hello"))
    finally:
        server.shutdown()
        server.server_close()
        bridge_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert status == 502
    assert payload["error"] == "upstream_strict_reply_missing_fields"
    assert "oauth-access" not in json.dumps(payload)


def test_bridge_upstream_timeout_is_safe_504(monkeypatch, tmp_path):
    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def fake_call_real_upstream(handler, payload):
        raise TimeoutError("timed out")

    try:
        _post_json(base_url + "/login", {"login": {"type": "chatgptAuthTokens", "accessToken": "oauth-access"}})
        monkeypatch.setattr(bridge, "call_real_upstream", fake_call_real_upstream)
        status, payload = _post_json(base_url + "/reply", _reply_with_login("hello"))
    finally:
        server.shutdown()
        server.server_close()
        bridge_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert status == 504
    assert payload == {"ok": False, "error": "upstream_timeout"}
    assert "oauth-access" not in json.dumps(payload)


def test_bridge_preserves_safe_upstream_status_and_error_class(monkeypatch, tmp_path):
    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_POST(self) -> None:
            raw = json.dumps({"ok": False, "error": "codex_queue_timeout", "error_stage": "queue", "stage_ms": {"queue": 751}}).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    bridge = _load_bridge_module(monkeypatch, tmp_path, upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}/reply")
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        status, payload = _post_json(base_url + "/reply", _reply_with_login("hello"))
    finally:
        server.shutdown()
        server.server_close()
        bridge_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert status == 429
    assert payload["error"] == "codex_queue_timeout"
    assert payload["bridge_error"] == "codex_upstream_http_error"
    assert payload["upstream_status"] == 429
    assert payload["upstream_error"] == "codex_queue_timeout"
    assert payload["error_stage"] == "queue"
    assert payload["stage_ms"]["queue"] == 751
    assert "oauth-access" not in json.dumps(payload)
