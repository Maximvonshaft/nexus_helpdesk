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
PORT = int(os.environ.get("PORT", "18796"))
MODEL_URL = os.environ.get("CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL", "").strip()
MODEL_TIMEOUT_SECONDS = float(os.environ.get("CODEX_PRIVATE_REPLY_ENGINE_MODEL_TIMEOUT_SECONDS", "30"))
READYZ_TIMEOUT_SECONDS = float(os.environ.get("CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS", "30"))
BACKEND_LABEL = os.environ.get("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "unconfigured").strip() or "unconfigured"
VERSION = "0.1"

_ALLOWED_INTENTS = {
    "greeting",
    "tracking",
    "tracking_missing_number",
    "tracking_unresolved",
    "complaint",
    "address_change",
    "handoff",
    "other",
}


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
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


def model_readyz_url() -> str | None:
    if not MODEL_URL:
        return None
    parsed = urlparse(MODEL_URL)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/readyz", "", "", ""))


def model_ready() -> bool:
    readyz_url = model_readyz_url()
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


def health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "nexus-codex-private-reply-engine",
        "version": VERSION,
    }


def readiness_payload() -> dict[str, Any]:
    model_configured = bool(MODEL_URL)
    model_reachable = model_configured and model_ready()
    backend_configured = BACKEND_LABEL not in {"", "unconfigured", "stub", "contract_fixture"}
    ok = model_configured and model_reachable and backend_configured
    reason = None
    if not model_configured:
        reason = "codex_private_reply_model_not_configured"
    elif not backend_configured:
        reason = "codex_reply_generation_backend_not_configured"
    elif not model_reachable:
        reason = "codex_private_reply_model_unreachable"
    return {
        "ok": ok,
        "service": "nexus-codex-private-reply-engine",
        "model_configured": model_configured,
        "model_reachable": model_reachable,
        "reply_generation_backend": BACKEND_LABEL if backend_configured else "unconfigured",
        "capabilities": {
            "reply_only": True,
            "browser_cookie_scraping": False,
            "chatgpt_session_scraping": False,
            "shell_execution": False,
            "tool_execution": False,
            "direct_ticket_action": False,
            "direct_order_action": False,
            "direct_customer_write": False,
        },
        "reason": reason,
        "version": VERSION,
    }


def validate_request_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("body"), str) or not payload.get("body", "").strip():
        raise ValueError("body_required")
    if payload.get("messages") is not None and not isinstance(payload.get("messages"), list):
        raise ValueError("messages_must_be_array")
    if payload.get("tracking_fact_summary") is not None and not isinstance(payload.get("tracking_fact_summary"), str):
        raise ValueError("tracking_fact_summary_invalid")
    if payload.get("tracking_fact_evidence_present") is not None and not isinstance(payload.get("tracking_fact_evidence_present"), bool):
        raise ValueError("tracking_fact_evidence_present_invalid")


def strict_reply(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("model_reply_response_must_be_object")
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
        raise ValueError("model_reply_missing_required_fields")
    if not isinstance(payload.get("reply"), str) or not payload.get("reply", "").strip():
        raise ValueError("model_reply_text_invalid")
    if not isinstance(payload.get("intent"), str) or payload["intent"] not in _ALLOWED_INTENTS:
        raise ValueError("model_reply_intent_invalid")
    if payload.get("tracking_number") is not None and not isinstance(payload.get("tracking_number"), str):
        raise ValueError("model_reply_tracking_number_invalid")
    if not isinstance(payload.get("handoff_required"), bool):
        raise ValueError("model_reply_handoff_required_invalid")
    if payload.get("handoff_reason") is not None and not isinstance(payload.get("handoff_reason"), str):
        raise ValueError("model_reply_handoff_reason_invalid")
    if payload.get("recommended_agent_action") is not None and not isinstance(payload.get("recommended_agent_action"), str):
        raise ValueError("model_reply_recommended_agent_action_invalid")
    return {
        "reply": payload["reply"].strip()[:1200],
        "intent": payload["intent"],
        "tracking_number": payload.get("tracking_number"),
        "handoff_required": payload["handoff_required"],
        "handoff_reason": payload.get("handoff_reason"),
        "recommended_agent_action": payload.get("recommended_agent_action"),
    }


def extract_model_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if {"reply", "intent", "tracking_number", "handoff_required", "handoff_reason", "recommended_agent_action"}.issubset(payload.keys()):
        return payload
    for key in ("output_text", "response_text", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("model_reply_invalid_json") from exc
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            try:
                return json.loads(message["content"])
            except json.JSONDecodeError as exc:
                raise ValueError("model_reply_invalid_json") from exc
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
        if parts:
            try:
                return json.loads("\n".join(parts))
            except json.JSONDecodeError as exc:
                raise ValueError("model_reply_invalid_json") from exc
    return payload


def model_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Speedaf logistics customer support reply engine. "
                    "Return only strict JSON with keys reply, intent, tracking_number, "
                    "handoff_required, handoff_reason, recommended_agent_action. "
                    "Do not perform actions, claim ticket/order changes, expose secrets, "
                    "or invent parcel status without trusted tracking evidence."
                ),
            },
            *(payload.get("messages") or []),
            {"role": "user", "content": payload["body"]},
        ],
        "body": payload["body"],
        "contract": payload.get("contract") or "speedaf_webchat_fast_reply_v1",
        "tracking_fact_summary": payload.get("tracking_fact_summary"),
        "tracking_fact_evidence_present": bool(payload.get("tracking_fact_evidence_present")),
        "chatgptAccountId": payload.get("chatgptAccountId"),
        "chatgptPlanType": payload.get("chatgptPlanType"),
        "response_contract": {
            "reply": "string",
            "intent": "greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other",
            "tracking_number": "string|null",
            "handoff_required": "boolean",
            "handoff_reason": "string|null",
            "recommended_agent_action": "string|null",
        },
    }


def call_model(payload: dict[str, Any], token: str) -> dict[str, Any]:
    if not MODEL_URL:
        raise RuntimeError("codex_private_reply_model_not_configured")
    raw_body = json.dumps(model_request_payload(payload), ensure_ascii=False).encode("utf-8")
    req = request.Request(
        MODEL_URL,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Nexus-Codex-Reply-Engine": "reply-only-v1",
        },
    )
    with request.urlopen(req, timeout=MODEL_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    decoded = json.loads(raw.decode("utf-8"))
    return strict_reply(extract_model_payload(decoded))


class Handler(BaseHTTPRequestHandler):
    server_version = "NexusCodexPrivateReplyEngine/" + VERSION

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
            json_response(self, 503, {"ok": False, "error": ready.get("reason") or "private_reply_engine_not_ready"})
            return
        try:
            validate_request_payload(payload)
            json_response(self, 200, call_model(payload, token))
        except error.HTTPError as exc:
            json_response(self, 502, {"ok": False, "error": "model_http_error", "upstream_status": exc.code})
        except (TimeoutError, socket.timeout):
            json_response(self, 504, {"ok": False, "error": "model_timeout"})
        except ValueError as exc:
            json_response(self, 502, {"ok": False, "error": str(exc)[:120]})
        except Exception:
            json_response(self, 502, {"ok": False, "error": "model_unavailable"})


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
