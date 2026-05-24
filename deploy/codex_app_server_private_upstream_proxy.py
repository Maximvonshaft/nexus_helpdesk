#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error, request
from urllib.parse import urlparse, urlunparse

BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "18795"))
PRIVATE_REPLY_URL = os.environ.get("CODEX_APP_SERVER_PRIVATE_REPLY_URL", "").strip()
PRIVATE_TIMEOUT_SECONDS = float(os.environ.get("CODEX_APP_SERVER_PRIVATE_TIMEOUT_SECONDS", "30"))
READYZ_TIMEOUT_SECONDS = float(os.environ.get("CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS", "2"))
BACKEND_LABEL = os.environ.get("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "unconfigured").strip() or "unconfigured"
VERSION = "0.1"


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


def bearer_token(handler: BaseHTTPRequestHandler) -> str | None:
    auth = handler.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    return token or None


def private_readyz_url() -> str | None:
    if not PRIVATE_REPLY_URL:
        return None
    parsed = urlparse(PRIVATE_REPLY_URL)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/readyz", "", "", ""))


def private_ready() -> bool:
    readyz_url = private_readyz_url()
    if not readyz_url:
        return False
    try:
        req = request.Request(readyz_url, method="GET", headers={"Accept": "application/json"})
        with request.urlopen(req, timeout=max(0.2, min(READYZ_TIMEOUT_SECONDS, 5.0))) as resp:
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


def health_payload() -> dict[str, Any]:
    return {"ok": True, "service": "nexus-codex-app-server-private-upstream-proxy", "version": VERSION}


def readiness_payload() -> dict[str, Any]:
    private_configured = bool(PRIVATE_REPLY_URL)
    private_reachable = private_configured and private_ready()
    backend_configured = BACKEND_LABEL not in {"", "unconfigured", "stub", "contract_fixture"}
    ok = private_configured and private_reachable and backend_configured
    reason = None
    if not private_configured:
        reason = "codex_private_reply_endpoint_not_configured"
    elif not backend_configured:
        reason = "codex_reply_generation_backend_not_configured"
    elif not private_reachable:
        reason = "codex_private_reply_endpoint_unreachable"
    return {
        "ok": ok,
        "service": "nexus-codex-app-server-private-upstream-proxy",
        "private_reply_configured": private_configured,
        "private_reply_reachable": private_reachable,
        "reply_generation_backend": BACKEND_LABEL if backend_configured else "unconfigured",
        "reason": reason,
        "version": VERSION,
    }


def strict_reply(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("private_reply_response_must_be_object")
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
        raise ValueError("private_reply_missing_required_fields")
    if not isinstance(payload.get("reply"), str) or not payload.get("reply", "").strip():
        raise ValueError("private_reply_text_invalid")
    if not isinstance(payload.get("intent"), str):
        raise ValueError("private_reply_intent_invalid")
    if payload.get("tracking_number") is not None and not isinstance(payload.get("tracking_number"), str):
        raise ValueError("private_reply_tracking_number_invalid")
    if not isinstance(payload.get("handoff_required"), bool):
        raise ValueError("private_reply_handoff_required_invalid")
    if payload.get("handoff_reason") is not None and not isinstance(payload.get("handoff_reason"), str):
        raise ValueError("private_reply_handoff_reason_invalid")
    if payload.get("recommended_agent_action") is not None and not isinstance(payload.get("recommended_agent_action"), str):
        raise ValueError("private_reply_recommended_agent_action_invalid")
    return {
        "reply": payload["reply"].strip(),
        "intent": payload["intent"],
        "tracking_number": payload.get("tracking_number"),
        "handoff_required": payload["handoff_required"],
        "handoff_reason": payload.get("handoff_reason"),
        "recommended_agent_action": payload.get("recommended_agent_action"),
    }


def forwarded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "body": payload.get("body"),
        "messages": payload.get("messages") or [],
        "contract": payload.get("contract"),
        "tracking_fact_summary": payload.get("tracking_fact_summary"),
        "tracking_fact_evidence_present": payload.get("tracking_fact_evidence_present"),
        "chatgptAccountId": payload.get("chatgptAccountId"),
        "chatgptPlanType": payload.get("chatgptPlanType"),
    }


def call_private_reply(payload: dict[str, Any], token: str) -> dict[str, Any]:
    if not PRIVATE_REPLY_URL:
        raise RuntimeError("codex_private_reply_endpoint_not_configured")
    raw_body = json.dumps(forwarded_payload(payload), ensure_ascii=False).encode("utf-8")
    req = request.Request(
        PRIVATE_REPLY_URL,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Nexus-Codex-Upstream": "private-reply-v1",
        },
    )
    with request.urlopen(req, timeout=PRIVATE_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    decoded = json.loads(raw.decode("utf-8"))
    return strict_reply(decoded)


class Handler(BaseHTTPRequestHandler):
    server_version = "NexusCodexAppServerPrivateUpstreamProxy/" + VERSION

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
        if self.path != "/reply":
            json_response(self, 404, {"ok": False, "error": "not_found"})
            return
        token = bearer_token(self)
        if not token:
            json_response(self, 401, {"ok": False, "error": "oauth_bearer_required"})
            return
        payload, err = read_json(self)
        if err:
            json_response(self, 400, {"ok": False, "error": err})
            return
        assert payload is not None
        ready = readiness_payload()
        if not ready["ok"]:
            json_response(self, 503, {"ok": False, "error": ready.get("reason") or "private_upstream_not_ready"})
            return
        try:
            json_response(self, 200, call_private_reply(payload, token))
        except error.HTTPError as exc:
            json_response(self, 502, {"ok": False, "error": "private_reply_http_error", "upstream_status": exc.code})
        except (TimeoutError, socket.timeout):
            json_response(self, 504, {"ok": False, "error": "private_reply_timeout"})
        except ValueError as exc:
            json_response(self, 502, {"ok": False, "error": str(exc)[:120]})
        except Exception:
            json_response(self, 502, {"ok": False, "error": "private_reply_unavailable"})


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
