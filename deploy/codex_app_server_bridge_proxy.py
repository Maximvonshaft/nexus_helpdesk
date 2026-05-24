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

BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18794"))
TOKEN_FILE = (
    os.environ.get("CODEX_APP_SERVER_TOKEN_FILE")
    or os.environ.get("TOKEN_FILE")
    or "/run/nexus/codex_app_server_bridge_token"
)
MODE = os.environ.get("CODEX_APP_SERVER_BRIDGE_MODE", "real").strip().lower()
REAL_UPSTREAM_URL = os.environ.get("CODEX_APP_SERVER_REAL_UPSTREAM_URL", "").strip()
REPLY_GENERATION_BACKEND = os.environ.get("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "unconfigured").strip() or "unconfigured"
UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS", "30"))
VERSION = "1.2"

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


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def safe_log(handler: BaseHTTPRequestHandler, message: str) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": handler.client_address[0] if handler.client_address else None,
        "method": getattr(handler, "command", None),
        "path": getattr(handler, "path", None),
        "message": message,
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)


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
    }


def readiness_payload() -> dict[str, Any]:
    token_configured = bool(load_bridge_token())
    real_upstream_configured = MODE == "real" and bool(REAL_UPSTREAM_URL)
    ok = token_configured and real_upstream_configured
    reason = None
    if not token_configured:
        reason = "bridge_token_not_configured"
    elif MODE != "real":
        reason = "codex_app_server_bridge_not_real"
    elif not REAL_UPSTREAM_URL:
        reason = "codex_app_server_real_upstream_not_configured"
    return {
        "ok": ok,
        "service": "nexus-codex-app-server-bridge-proxy",
        "mode": MODE,
        "real_upstream_configured": real_upstream_configured,
        "reason": reason,
        "accepts_oauth_login": True,
        "reply_generation_backend": REPLY_GENERATION_BACKEND if real_upstream_configured else "unconfigured",
        "token_file_configured": token_configured,
        "oauth_session_present": bool(_LOGIN_STATE["access_token"]),
        "version": VERSION,
    }


def normalize_reply(payload: dict[str, Any]) -> dict[str, Any]:
    reply = payload.get("reply") or payload.get("customer_reply") or payload.get("text") or payload.get("answer")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("upstream_reply_missing")
    return {
        "reply": reply.strip(),
        "intent": payload.get("intent") if isinstance(payload.get("intent"), str) else "other",
        "tracking_number": payload.get("tracking_number") if isinstance(payload.get("tracking_number"), str) else None,
        "handoff_required": payload.get("handoff_required") if isinstance(payload.get("handoff_required"), bool) else False,
        "handoff_reason": payload.get("handoff_reason") if isinstance(payload.get("handoff_reason"), str) else None,
        "recommended_agent_action": payload.get("recommended_agent_action") if isinstance(payload.get("recommended_agent_action"), str) else None,
    }


def call_real_upstream(payload: dict[str, Any]) -> dict[str, Any]:
    access_token = _LOGIN_STATE.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("codex_login_required")
    upstream_payload = {
        "body": payload.get("body"),
        "messages": payload.get("messages") or [],
        "contract": payload.get("contract"),
        "tracking_fact_summary": payload.get("tracking_fact_summary"),
        "tracking_fact_evidence_present": payload.get("tracking_fact_evidence_present"),
        "chatgptAccountId": _LOGIN_STATE.get("chatgpt_account_id"),
        "chatgptPlanType": _LOGIN_STATE.get("chatgpt_plan_type"),
    }
    raw_body = json.dumps(upstream_payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        REAL_UPSTREAM_URL,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "X-Nexus-Codex-Bridge": "oauth-session",
        },
    )
    with request.urlopen(req, timeout=UPSTREAM_TIMEOUT_SECONDS) as resp:
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
            _LOGIN_STATE.update({
                "access_token": str(login["accessToken"]),
                "chatgpt_account_id": login.get("chatgptAccountId"),
                "chatgpt_plan_type": login.get("chatgptPlanType"),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            json_response(self, 200, {"ok": True})
            return

        ready = readiness_payload()
        if not ready["ok"]:
            json_response(self, 503, {"ok": False, "error": ready.get("reason") or "bridge_not_ready", "readiness": ready})
            return
        try:
            json_response(self, 200, call_real_upstream(payload))
        except error.HTTPError as exc:
            json_response(self, 502, {"ok": False, "error": "upstream_http_error", "upstream_status": exc.code})
        except (TimeoutError, socket.timeout):
            json_response(self, 504, {"ok": False, "error": "upstream_timeout"})
        except Exception as exc:
            json_response(self, 502, {"ok": False, "error": str(exc)[:120]})


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
