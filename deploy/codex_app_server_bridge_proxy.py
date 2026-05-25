#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse, urlunparse

BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18794"))
TOKEN_FILE = (
    os.environ.get("CODEX_APP_SERVER_TOKEN_FILE")
    or os.environ.get("TOKEN_FILE")
    or "/run/nexus/codex_app_server_bridge_token"
)
MODE = os.environ.get("CODEX_APP_SERVER_BRIDGE_MODE", "real").strip().lower()
RUNTIME_BACKEND = os.environ.get("CODEX_APP_SERVER_RUNTIME_BACKEND", "python_cli_pool").strip().lower() or "python_cli_pool"
LEGACY_REAL_UPSTREAM_URL = os.environ.get("CODEX_APP_SERVER_REAL_UPSTREAM_URL", "").strip()
REAL_UPSTREAM_URL_PYTHON = os.environ.get("CODEX_APP_SERVER_REAL_UPSTREAM_URL_PYTHON", "").strip()
REAL_UPSTREAM_URL_NODE = os.environ.get("CODEX_APP_SERVER_REAL_UPSTREAM_URL_NODE", "").strip()
REPLY_GENERATION_BACKEND = os.environ.get("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "").strip()
UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS", "9"))
READYZ_TIMEOUT_SECONDS = float(os.environ.get("CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS", "30"))
AUTH_MODE = os.environ.get("CODEX_APP_SERVER_AUTH_MODE", "per_request").strip().lower() or "per_request"
LEGACY_LOGIN_STATE_ENABLED = os.environ.get("CODEX_APP_SERVER_LEGACY_LOGIN_STATE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
GIT_SHA = os.environ.get("GIT_SHA", "unknown")
IMAGE_TAG = os.environ.get("IMAGE_TAG", "unknown")
APP_VERSION = os.environ.get("APP_VERSION", "unknown")
VERSION = "1.2"


def resolve_real_upstream_url() -> str:
    if RUNTIME_BACKEND == "node_appserver":
        return REAL_UPSTREAM_URL_NODE or "http://codex-appserver-runtime:18810/reply"
    return REAL_UPSTREAM_URL_PYTHON or LEGACY_REAL_UPSTREAM_URL or "http://codex-private-model-runtime:18800/reply"


def resolve_reply_generation_backend() -> str:
    if REPLY_GENERATION_BACKEND:
        return REPLY_GENERATION_BACKEND
    if RUNTIME_BACKEND == "node_appserver":
        return "nexus_codex_appserver_runtime"
    if RUNTIME_BACKEND == "python_cli_pool":
        return "python_cli_pool"
    return "unconfigured"


REAL_UPSTREAM_URL = resolve_real_upstream_url()
EFFECTIVE_REPLY_GENERATION_BACKEND = resolve_reply_generation_backend()

_LOGIN_STATE: dict[str, Any] = {
    "access_token": None,
    "chatgpt_account_id": None,
    "chatgpt_plan_type": None,
    "updated_at": None,
}


def load_bridge_token() -> str:
    try:
        value = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    return value


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            handler.send_header(key, str(value))
        handler.end_headers()
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError):
        return


def safe_log(handler: BaseHTTPRequestHandler, message: str) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": handler.client_address[0] if handler.client_address else None,
        "method": getattr(handler, "command", None),
        "path": getattr(handler, "path", None),
        "message": message,
    }
    try:
        print(json.dumps(record, ensure_ascii=False), flush=True)
    except (BrokenPipeError, OSError):
        return


def check_bind_host() -> None:
    if BIND_HOST not in {"0.0.0.0", "127.0.0.1", "172.18.0.1", "::1"}:
        raise SystemExit("BIND_HOST must be 0.0.0.0, 127.0.0.1, 172.18.0.1, or ::1")


def check_auth(handler: BaseHTTPRequestHandler) -> bool:
    expected = load_bridge_token()
    if not expected:
        json_response(handler, 503, {"ok": False, "error": "bridge_token_not_configured"})
        return False
    auth = handler.headers.get("Authorization", "")
    token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
    if token != expected:
        json_response(handler, 401, {"ok": False, "error": "unauthorized"})
        return False
    return True


def read_json(handler: BaseHTTPRequestHandler) -> tuple[dict[str, Any] | None, str | None]:
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        return None, "invalid_content_length"
    if length <= 0:
        return None, "empty_body"
    try:
        payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    except Exception:
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "body_must_be_object"
    return payload, None


def health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "nexus-codex-app-server-bridge-proxy",
        "version": VERSION,
        "git_sha": GIT_SHA,
        "image_tag": IMAGE_TAG,
        "app_version": APP_VERSION,
    }


def upstream_readyz_url() -> str | None:
    if not REAL_UPSTREAM_URL:
        return None
    parsed = urlparse(REAL_UPSTREAM_URL)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/readyz", "", "", ""))


def upstream_ready() -> bool:
    readyz_url = upstream_readyz_url()
    if not readyz_url:
        return False
    try:
        req = request.Request(readyz_url, method="GET", headers={"Accept": "application/json"})
        with request.urlopen(req, timeout=max(0.2, min(READYZ_TIMEOUT_SECONDS, 60.0))) as resp:
            raw = resp.read()
            if resp.status != 200:
                return False
    except Exception:
        return False
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except Exception:
        return True
    return not isinstance(decoded, dict) or decoded.get("ok") is not False


def readiness_payload() -> dict[str, Any]:
    token_configured = bool(load_bridge_token())
    backend_supported = RUNTIME_BACKEND in {"python_cli_pool", "node_appserver"}
    real_upstream_configured = MODE == "real" and backend_supported and bool(REAL_UPSTREAM_URL)
    real_upstream_reachable = real_upstream_configured and upstream_ready()
    ok = token_configured and real_upstream_configured and real_upstream_reachable
    reason = None
    if not token_configured:
        reason = "bridge_token_not_configured"
    elif MODE != "real":
        reason = "codex_app_server_bridge_not_real"
    elif not backend_supported:
        reason = "codex_app_server_runtime_backend_invalid"
    elif not REAL_UPSTREAM_URL:
        reason = "codex_app_server_real_upstream_not_configured"
    elif not real_upstream_reachable:
        reason = "codex_app_server_real_upstream_unreachable"
    return {
        "ok": ok,
        "service": "nexus-codex-app-server-bridge-proxy",
        "mode": MODE,
        "real_upstream_configured": real_upstream_configured,
        "real_upstream_reachable": real_upstream_reachable,
        "runtime_backend": RUNTIME_BACKEND,
        "real_upstream_url": REAL_UPSTREAM_URL,
        "reason": reason,
        "accepts_oauth_login": True,
        "reply_generation_backend": EFFECTIVE_REPLY_GENERATION_BACKEND if real_upstream_configured else "unconfigured",
        "token_file_configured": token_configured,
        "oauth_session_present": bool(_LOGIN_STATE["access_token"]),
        "auth_mode": AUTH_MODE,
        "legacy_login_state_enabled": LEGACY_LOGIN_STATE_ENABLED,
        "version": VERSION,
        "git_sha": GIT_SHA,
        "image_tag": IMAGE_TAG,
        "app_version": APP_VERSION,
    }


def normalize_reply(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "reply",
        "intent",
        "tracking_number",
        "handoff_required",
        "handoff_reason",
        "recommended_agent_action",
    }
    missing = sorted(required - set(payload.keys()))
    if missing:
        raise ValueError("upstream_strict_reply_missing_fields")
    reply = payload.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("upstream_reply_missing")
    if not isinstance(payload.get("intent"), str):
        raise ValueError("upstream_intent_invalid")
    if payload.get("tracking_number") is not None and not isinstance(payload.get("tracking_number"), str):
        raise ValueError("upstream_tracking_number_invalid")
    if not isinstance(payload.get("handoff_required"), bool):
        raise ValueError("upstream_handoff_required_invalid")
    if payload.get("handoff_reason") is not None and not isinstance(payload.get("handoff_reason"), str):
        raise ValueError("upstream_handoff_reason_invalid")
    if payload.get("recommended_agent_action") is not None and not isinstance(payload.get("recommended_agent_action"), str):
        raise ValueError("upstream_recommended_agent_action_invalid")
    return {
        "reply": reply.strip(),
        "intent": payload["intent"],
        "tracking_number": payload.get("tracking_number"),
        "handoff_required": payload["handoff_required"],
        "handoff_reason": payload.get("handoff_reason"),
        "recommended_agent_action": payload.get("recommended_agent_action"),
    }


def _login_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    login = payload.get("login")
    if not isinstance(login, dict):
        return None
    if login.get("type") != "chatgptAuthTokens" or not login.get("accessToken"):
        return None
    return login


def _legacy_login() -> dict[str, Any] | None:
    if not LEGACY_LOGIN_STATE_ENABLED:
        return None
    access_token = _LOGIN_STATE.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    return {
        "accessToken": access_token,
        "chatgptAccountId": _LOGIN_STATE.get("chatgpt_account_id"),
        "chatgptPlanType": _LOGIN_STATE.get("chatgpt_plan_type"),
    }


def _remaining_timeout_seconds(handler: BaseHTTPRequestHandler, configured_timeout: float) -> tuple[float, int]:
    header_value = handler.headers.get("X-Nexus-Request-Deadline-Ms", "").strip()
    if not header_value:
        timeout = max(0.05, min(configured_timeout, 10.0))
        return timeout, int(timeout * 1000)
    try:
        deadline_ms = int(header_value)
    except ValueError as exc:
        raise TimeoutError("invalid_deadline") from exc
    remaining_ms = deadline_ms - int(time.time() * 1000)
    if remaining_ms <= 0:
        raise TimeoutError("deadline_exceeded")
    timeout = max(0.05, min(configured_timeout, remaining_ms / 1000.0))
    return timeout, remaining_ms


def _forward_headers(handler: BaseHTTPRequestHandler, token: str, budget_ms: int) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Nexus-Codex-Bridge": "oauth-session",
        "X-Nexus-Codex-Timeout-Budget-Ms": str(max(0, budget_ms)),
    }
    request_id = handler.headers.get("X-Nexus-Request-Id")
    deadline = handler.headers.get("X-Nexus-Request-Deadline-Ms")
    if request_id:
        headers["X-Nexus-Request-Id"] = request_id
    if deadline:
        headers["X-Nexus-Request-Deadline-Ms"] = deadline
    return headers


def call_real_upstream(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> dict[str, Any]:
    login = _login_from_payload(payload) or _legacy_login()
    if not login:
        raise RuntimeError("codex_login_required")
    access_token = str(login["accessToken"])
    timeout, budget_ms = _remaining_timeout_seconds(handler, UPSTREAM_TIMEOUT_SECONDS)
    setattr(handler, "_nexus_codex_budget_ms", budget_ms)
    upstream_payload = {
        "login": {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": login.get("chatgptAccountId"),
            "chatgptPlanType": login.get("chatgptPlanType"),
        },
        "body": payload.get("body"),
        "messages": payload.get("messages") or [],
        "contract": payload.get("contract"),
        "tracking_fact_summary": payload.get("tracking_fact_summary"),
        "tracking_fact_evidence_present": payload.get("tracking_fact_evidence_present"),
        "tenant_id": payload.get("tenant_id"),
        "channel_key": payload.get("channel_key"),
        "session_id": payload.get("session_id"),
        "chatgptAccountId": login.get("chatgptAccountId"),
        "chatgptPlanType": login.get("chatgptPlanType"),
    }
    raw_body = json.dumps(upstream_payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        REAL_UPSTREAM_URL,
        data=raw_body,
        method="POST",
        headers=_forward_headers(handler, access_token, budget_ms),
    )
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("upstream_response_must_be_object")
    return normalize_reply(decoded)


class Handler(BaseHTTPRequestHandler):
    server_version = "NexusCodexAppServerBridgeProxy/" + VERSION

    def log_message(self, fmt: str, *args) -> None:
        safe_log(self, fmt % args)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            json_response(self, 200, health_payload())
            return
        if self.path == "/readyz":
            payload = readiness_payload()
            json_response(self, 200 if payload["ok"] else 503, payload)
            return
        json_response(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path not in {"/login", "/reply"}:
            json_response(self, 404, {"ok": False, "error": "not_found"})
            return
        if not check_auth(self):
            return
        payload, err = read_json(self)
        if err:
            json_response(self, 400, {"ok": False, "error": err})
            return
        assert payload is not None

        if self.path == "/login":
            login = payload.get("login")
            if not isinstance(login, dict) or login.get("type") != "chatgptAuthTokens" or not login.get("accessToken"):
                json_response(self, 400, {"ok": False, "error": "invalid_login_payload"})
                return
            if LEGACY_LOGIN_STATE_ENABLED:
                _LOGIN_STATE.update({
                    "access_token": str(login["accessToken"]),
                    "chatgpt_account_id": login.get("chatgptAccountId"),
                    "chatgpt_plan_type": login.get("chatgptPlanType"),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
            json_response(self, 200, {"ok": True})
            return

        started = time.monotonic()
        if not load_bridge_token():
            json_response(self, 503, {"ok": False, "error": "bridge_token_not_configured"})
            return
        if MODE != "real":
            json_response(self, 503, {"ok": False, "error": "codex_app_server_bridge_not_real"})
            return
        if not REAL_UPSTREAM_URL:
            json_response(self, 503, {"ok": False, "error": "codex_app_server_real_upstream_not_configured"})
            return
        if RUNTIME_BACKEND not in {"python_cli_pool", "node_appserver"}:
            json_response(self, 503, {"ok": False, "error": "codex_app_server_runtime_backend_invalid"})
            return
        if EFFECTIVE_REPLY_GENERATION_BACKEND in {"", "stub", "unconfigured", "contract_fixture"}:
            json_response(self, 503, {"ok": False, "error": "codex_reply_generation_backend_not_configured"})
            return
        try:
            reply = call_real_upstream(self, payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            budget_ms = getattr(self, "_nexus_codex_budget_ms", "")
            json_response(
                self,
                200,
                reply,
                {
                    "X-Nexus-Codex-Elapsed-Ms": str(elapsed_ms),
                    "X-Nexus-Codex-Backend": EFFECTIVE_REPLY_GENERATION_BACKEND,
                    "X-Nexus-Codex-Timeout-Budget-Ms": budget_ms,
                },
            )
        except error.HTTPError as exc:
            json_response(self, 502, {"ok": False, "error": "upstream_http_error", "upstream_status": exc.code}, {"X-Nexus-Codex-Elapsed-Ms": str(int((time.monotonic() - started) * 1000)), "X-Nexus-Codex-Backend": EFFECTIVE_REPLY_GENERATION_BACKEND})
        except (TimeoutError, socket.timeout):
            json_response(self, 504, {"ok": False, "error": "upstream_timeout"}, {"X-Nexus-Codex-Elapsed-Ms": str(int((time.monotonic() - started) * 1000)), "X-Nexus-Codex-Backend": EFFECTIVE_REPLY_GENERATION_BACKEND})
        except Exception as exc:
            json_response(self, 502, {"ok": False, "error": str(exc)[:120]}, {"X-Nexus-Codex-Elapsed-Ms": str(int((time.monotonic() - started) * 1000)), "X-Nexus-Codex-Backend": EFFECTIVE_REPLY_GENERATION_BACKEND})


def main() -> None:
    check_bind_host()
    startup = readiness_payload()
    startup.update({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bind": BIND_HOST,
        "port": PORT,
    })
    print(json.dumps(startup, ensure_ascii=False), flush=True)
    ThreadingHTTPServer((BIND_HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
