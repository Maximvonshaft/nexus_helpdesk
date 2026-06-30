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


def _load_engine_module(
    monkeypatch,
    tmp_path,
    *,
    model_url: str = "",
    backend: str = "nexus_private_reply_engine",
    readyz_timeout: str = "1",
):
    monkeypatch.setenv("CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL", model_url)
    monkeypatch.setenv("CODEX_PRIVATE_REPLY_ENGINE_MODEL_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS", readyz_timeout)
    monkeypatch.setenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", backend)
    spec = importlib.util.spec_from_file_location(
        f"codex_private_reply_engine_test_{id(tmp_path)}",
        ROOT / "deploy" / "codex_private_reply_engine.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_upstream_module(monkeypatch, tmp_path, *, private_url: str, backend: str = "nexus_private_reply_engine"):
    monkeypatch.setenv("CODEX_APP_SERVER_PRIVATE_REPLY_URL", private_url)
    monkeypatch.setenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", backend)
    monkeypatch.setenv("CODEX_APP_SERVER_PRIVATE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS", "1")
    spec = importlib.util.spec_from_file_location(
        f"codex_app_server_private_upstream_proxy_chain_test_{id(tmp_path)}",
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


def _strict_reply(reply: str = "Please share your tracking number so I can check your parcel status.") -> dict:
    return {
        "reply": reply,
        "intent": "tracking_missing_number",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
    }


def _reply_payload(body: str = "Where is my parcel?") -> dict:
    return {
        "body": body,
        "messages": [{"role": "user", "content": body}],
        "contract": "speedaf_webchat_fast_reply_v1",
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "chatgptAccountId": "acct-1",
        "chatgptPlanType": "plus",
    }


def test_private_reply_engine_healthz_is_liveness_only(monkeypatch, tmp_path):
    engine = _load_engine_module(monkeypatch, tmp_path, model_url="")
    server = engine.ThreadingHTTPServer(("127.0.0.1", 0), engine.Handler)
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
    assert payload["service"] == "nexus-codex-private-reply-engine"


def test_private_reply_engine_readyz_is_200_when_model_configured(monkeypatch, tmp_path):
    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    try:
        engine = _load_engine_module(monkeypatch, tmp_path, model_url=f"http://127.0.0.1:{model.server_address[1]}/reply")
        payload = engine.readiness_payload()
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=2)

    assert payload["ok"] is True
    assert payload["model_configured"] is True
    assert payload["model_reachable"] is True
    assert payload["reply_generation_backend"] == "nexus_private_reply_engine"
    assert payload["capabilities"]["reply_only"] is True
    assert payload["capabilities"]["shell_execution"] is False


def test_private_reply_engine_readyz_timeout_30_is_honored(monkeypatch, tmp_path):
    engine = _load_engine_module(
        monkeypatch,
        tmp_path,
        model_url="http://codex-private-model-runtime:18800/reply",
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

    monkeypatch.setattr(engine.request, "urlopen", fake_urlopen)

    assert engine.model_ready() is True
    assert observed["timeout"] == 30


def test_private_reply_engine_slow_model_readyz_within_timeout_is_reachable(monkeypatch, tmp_path):
    class ModelHandler(BaseHTTPRequestHandler):
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

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    try:
        engine = _load_engine_module(
            monkeypatch,
            tmp_path,
            model_url=f"http://127.0.0.1:{model.server_address[1]}/reply",
            readyz_timeout="1",
        )
        payload = engine.readiness_payload()
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=2)

    assert payload["ok"] is True
    assert payload["model_reachable"] is True


def test_private_reply_engine_model_readyz_timeout_is_not_reachable(monkeypatch, tmp_path):
    engine = _load_engine_module(
        monkeypatch,
        tmp_path,
        model_url="http://codex-private-model-runtime:18800/reply",
        readyz_timeout="30",
    )

    def fake_urlopen(req, timeout):
        raise socket.timeout("timed out")

    monkeypatch.setattr(engine.request, "urlopen", fake_urlopen)
    payload = engine.readiness_payload()

    assert payload["ok"] is False
    assert payload["model_reachable"] is False
    assert payload["reason"] == "codex_private_reply_model_unreachable"


def test_private_reply_engine_rejects_stub_backend_labels(monkeypatch, tmp_path):
    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return None

        def do_GET(self) -> None:
            raw = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    try:
        for label in ("unconfigured", "stub", "contract_fixture"):
            engine = _load_engine_module(
                monkeypatch,
                tmp_path,
                model_url=f"http://127.0.0.1:{model.server_address[1]}/reply",
                backend=label,
            )
            payload = engine.readiness_payload()
            assert payload["ok"] is False
            assert payload["reason"] == "codex_reply_generation_backend_not_configured"
            assert payload["reply_generation_backend"] == "unconfigured"
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=2)


def test_private_reply_engine_reply_returns_strict_json(monkeypatch, tmp_path):
    captured: dict = {}

    class ModelHandler(BaseHTTPRequestHandler):
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
            raw = json.dumps(_strict_reply()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    engine = _load_engine_module(monkeypatch, tmp_path, model_url=f"http://127.0.0.1:{model.server_address[1]}/reply")
    server = engine.ThreadingHTTPServer(("127.0.0.1", 0), engine.Handler)
    engine_thread = threading.Thread(target=server.serve_forever, daemon=True)
    engine_thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", _reply_payload())
    finally:
        server.shutdown()
        server.server_close()
        engine_thread.join(timeout=2)
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=2)

    assert status == 200
    assert payload == _strict_reply()
    assert captured["authorization"].split(" ", 1) == ["Bearer", "oauth-access"]
    assert captured["payload"]["contract"] == "speedaf_webchat_fast_reply_v1"
    assert captured["payload"]["chatgptAccountId"] == "acct-1"
    assert captured["payload"]["response_contract"]["handoff_required"] == "boolean"
    assert "Return only strict JSON" in captured["payload"]["messages"][0]["content"]
    assert "Do not perform actions" in captured["payload"]["messages"][0]["content"]
    assert "oauth-access" not in json.dumps(payload)


def test_private_reply_engine_invalid_model_output_is_rejected(monkeypatch, tmp_path):
    class ModelHandler(BaseHTTPRequestHandler):
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
            raw = json.dumps({"output_text": "not-json"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    engine = _load_engine_module(monkeypatch, tmp_path, model_url=f"http://127.0.0.1:{model.server_address[1]}/reply")
    server = engine.ThreadingHTTPServer(("127.0.0.1", 0), engine.Handler)
    engine_thread = threading.Thread(target=server.serve_forever, daemon=True)
    engine_thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", _reply_payload())
    finally:
        server.shutdown()
        server.server_close()
        engine_thread.join(timeout=2)
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=2)

    assert status == 502
    assert payload["error"] == "model_reply_invalid_json"
    assert "oauth-access" not in json.dumps(payload)


def test_private_reply_engine_timeout_returns_fail_closed(monkeypatch, tmp_path):
    engine = _load_engine_module(monkeypatch, tmp_path, model_url="http://127.0.0.1:9/reply")
    monkeypatch.setattr(engine, "readiness_payload", lambda: {"ok": True})
    monkeypatch.setattr(engine, "call_model", lambda payload, token: (_ for _ in ()).throw(TimeoutError("timed out")))
    server = engine.ThreadingHTTPServer(("127.0.0.1", 0), engine.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{server.server_address[1]}/reply", _reply_payload())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 504
    assert payload == {"ok": False, "error": "model_timeout"}


def test_private_reply_engine_nonce_smoke_passes_when_model_returns_nonce(monkeypatch, tmp_path):
    nonce = "nonce-private-engine"

    class ModelHandler(BaseHTTPRequestHandler):
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
            raw = json.dumps(_strict_reply(f"Echo {nonce}")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    engine = _load_engine_module(monkeypatch, tmp_path, model_url=f"http://127.0.0.1:{model.server_address[1]}/reply")
    server = engine.ThreadingHTTPServer(("127.0.0.1", 0), engine.Handler)
    engine_thread = threading.Thread(target=server.serve_forever, daemon=True)
    engine_thread.start()
    try:
        status, payload = _post_json(
            f"http://127.0.0.1:{server.server_address[1]}/reply",
            _reply_payload(f"Echo this nonce exactly: {nonce}"),
        )
    finally:
        server.shutdown()
        server.server_close()
        engine_thread.join(timeout=2)
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=2)

    assert status == 200
    assert nonce in payload["reply"]


def test_18795_proxy_to_private_reply_engine_nonce_smoke_passes(monkeypatch, tmp_path):
    nonce = "nonce-through-18795"
    captured: dict = {}

    class ModelHandler(BaseHTTPRequestHandler):
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
            raw = json.dumps(_strict_reply(f"Echo {nonce}")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    engine = _load_engine_module(monkeypatch, tmp_path, model_url=f"http://127.0.0.1:{model.server_address[1]}/reply")
    engine_server = engine.ThreadingHTTPServer(("127.0.0.1", 0), engine.Handler)
    engine_thread = threading.Thread(target=engine_server.serve_forever, daemon=True)
    engine_thread.start()
    upstream = _load_upstream_module(
        monkeypatch,
        tmp_path,
        private_url=f"http://127.0.0.1:{engine_server.server_address[1]}/reply",
    )
    upstream_server = upstream.ThreadingHTTPServer(("127.0.0.1", 0), upstream.Handler)
    upstream_thread = threading.Thread(target=upstream_server.serve_forever, daemon=True)
    upstream_thread.start()
    try:
        ready = upstream.readiness_payload()
        status, payload = _post_json(
            f"http://127.0.0.1:{upstream_server.server_address[1]}/reply",
            _reply_payload(f"Echo this nonce exactly: {nonce}"),
        )
    finally:
        upstream_server.shutdown()
        upstream_server.server_close()
        upstream_thread.join(timeout=2)
        engine_server.shutdown()
        engine_server.server_close()
        engine_thread.join(timeout=2)
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=2)

    assert ready["ok"] is True
    assert status == 200
    assert nonce in payload["reply"]
    assert captured["authorization"].split(" ", 1) == ["Bearer", "oauth-access"]
    rendered = json.dumps(payload) + json.dumps(ready)
    assert "oauth-access" not in rendered


def test_private_reply_engine_does_not_mutate_canary_configuration():
    source = (ROOT / "deploy" / "codex_private_reply_engine.py").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "docker-compose.server.yml").read_text(encoding="utf-8")

    assert "provider_routing_rules" not in source
    assert "canary_percent" not in source
    assert "CODEX_APP_SERVER_CANARY_PERCENT" not in compose


def test_codex_chat_smoke_runbook_documents_real_private_model_gate():
    runbook = (ROOT / "docs" / "engineering" / "codex_chat_smoke_runbook.md").read_text(encoding="utf-8")

    assert "CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL=http://codex-private-reply-engine:18796/reply" in runbook
    assert "GET http://codex-private-reply-engine:18796/readyz" in runbook
    assert "18796 /readyz" in runbook or "http://127.0.0.1:18796/readyz" in runbook
    assert "18795/readyz" in runbook
    assert "18794/readyz" in runbook
    assert "SMOKE_HTTP_CODE=200" in runbook
    assert "nonce_echoed=True" in runbook
    assert "VERDICT=CODEX_AUTH_AND_CHAT_MODEL_CALL_CONNECTED" in runbook
    assert "canary_percent=0" in runbook
    assert "provider_runtime fallback remains configured" in runbook
    assert "rule_engine fallback" in runbook
