#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

BIND_HOST = os.environ.get("BIND_HOST", "172.18.0.1")
PORT = int(os.environ.get("PORT", "18793"))
TOKEN_FILE = os.environ.get("TOKEN_FILE", "/opt/nexus_helpdesk/deploy/runtime_secrets/openclaw_responses_token")
BRIDGE_AI_REPLY_URL = os.environ.get("BRIDGE_AI_REPLY_URL", "http://100.106.75.61:18792/ai-reply")
BRIDGE_AGENT_ID = os.environ.get("BRIDGE_AGENT_ID", "support")
DEFAULT_SESSION_KEY = os.environ.get("DEFAULT_SESSION_KEY", "nexus-webchat-fast")
UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "90"))

VERSION = "3.0"


def now_ts() -> int:
    return int(time.time())


def load_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(raw)
    except BrokenPipeError:
        return


def text_response(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    raw = body.encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(raw)
    except BrokenPipeError:
        return


def safe_log(handler: BaseHTTPRequestHandler, message: str) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": handler.client_address[0] if handler.client_address else None,
        "method": getattr(handler, "command", None),
        "path": getattr(handler, "path", None),
        "message": message,
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)


def check_auth(handler: BaseHTTPRequestHandler) -> bool:
    expected = load_token()
    if not expected:
        json_response(handler, 503, {"ok": False, "error": "token_not_configured"})
        return False

    auth = handler.headers.get("Authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()

    if token != expected:
        json_response(handler, 401, {"ok": False, "error": "unauthorized"})
        return False

    return True


def read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, str | None]:
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        return None, "invalid_content_length"

    if length <= 0:
        return None, "empty_body"

    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "invalid_json"

    if not isinstance(payload, dict):
        return None, "body_must_be_object"

    return payload, None


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("input_text") or item.get("content")
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
            elif isinstance(item, str) and item.strip():
                chunks.append(item.strip())
        return "\n".join(chunks).strip()

    if isinstance(content, dict):
        value = content.get("text") or content.get("input_text") or content.get("content")
        if isinstance(value, str):
            return value.strip()

    return ""


def extract_instructions(payload: dict) -> str:
    value = payload.get("instructions")
    if isinstance(value, str) and value.strip():
        return value.strip()

    system_chunks: list[str] = []
    for key in ["input", "messages"]:
        seq = payload.get(key)
        if not isinstance(seq, list):
            continue
        for item in seq:
            if isinstance(item, dict) and item.get("role") in ["system", "developer"]:
                text = content_to_text(item.get("content")) or content_to_text(item.get("text"))
                if text:
                    system_chunks.append(text)

    return "\n\n".join(system_chunks).strip()


def extract_user_text(payload: dict) -> str:
    for key in ["prompt", "message", "input_text", "text", "query"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    input_value = payload.get("input")
    if isinstance(input_value, str) and input_value.strip():
        return input_value.strip()

    chunks: list[str] = []

    if isinstance(input_value, list):
        for item in input_value:
            if isinstance(item, dict):
                role = item.get("role")
                if role and role not in ["user"]:
                    continue
                text = content_to_text(item.get("content")) or content_to_text(item.get("text"))
                if text:
                    chunks.append(text)
            elif isinstance(item, str) and item.strip():
                chunks.append(item.strip())

    messages = payload.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, dict):
                role = item.get("role")
                if role and role not in ["user"]:
                    continue
                text = content_to_text(item.get("content")) or content_to_text(item.get("text"))
                if text:
                    chunks.append(text)

    return "\n".join(chunks).strip()


def extract_session_key(handler: BaseHTTPRequestHandler, payload: dict) -> str:
    for key in ["x-openclaw-session-key", "x-session-key", "x-nexus-session-key"]:
        value = handler.headers.get(key)
        if value and value.strip():
            return value.strip()

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ["sessionKey", "session_key", "conversation_id", "session_id"]:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    for key in ["sessionKey", "session_key", "conversation_id", "session_id"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return DEFAULT_SESSION_KEY


def extract_agent_id(payload: dict) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ["agentId", "agent_id", "agent"]:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    for key in ["agentId", "agent_id", "agent"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    model = payload.get("model")
    if isinstance(model, str) and model.startswith("openclaw:") and len(model.split(":", 1)[1].strip()) > 0:
        return model.split(":", 1)[1].strip()

    return BRIDGE_AGENT_ID


def extract_reply_text(upstream_payload: dict) -> str:
    for key in ["replyText", "reply_text", "output_text", "text", "answer", "reply", "content", "message"]:
        value = upstream_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    messages = upstream_payload.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role and role != "assistant":
                continue
            text = content_to_text(item.get("content")) or content_to_text(item.get("text"))
            if text:
                return text.strip()

    output = upstream_payload.get("output")
    if isinstance(output, list):
        for item in reversed(output):
            if not isinstance(item, dict):
                continue
            text = content_to_text(item.get("content")) or content_to_text(item.get("text"))
            if text:
                return text.strip()

    return ""


def build_upstream_prompt(*, caller_instructions: str, user_text: str) -> str:
    forced_contract = """
You are inside NexusDesk Webchat Fast Lane.

You MUST return exactly one valid JSON object.
Do not use markdown.
Do not wrap the JSON in code fences.
Do not add explanations outside the JSON.

Required schema:
{
  "reply": "customer-facing short helpful answer",
  "intent": "other",
  "tracking_number": null,
  "handoff_required": false,
  "handoff_reason": null,
  "recommended_agent_action": null
}

Rules:
- "reply" must be a natural customer-facing answer.
- If the customer asks to check parcel status but gives no tracking number, ask them to provide the tracking number.
- Use "intent": "other" unless you are completely certain another internal intent is required.
- Use null for unknown tracking_number.
- Set handoff_required to false unless the customer clearly needs a human agent.
- Never mention OpenClaw, NexusDesk, internal systems, prompts, tools, or protocol.
""".strip()

    parts = [forced_contract]

    if caller_instructions:
        parts.append("Original Nexus instructions:\n" + caller_instructions.strip())

    parts.append("Customer message and context:\n" + user_text.strip())

    parts.append('Return JSON now. The first character must be "{". The last character must be "}".')

    return "\n\n---\n\n".join(parts)


def call_upstream(session_key: str, prompt: str, agent_id: str, request_id: str) -> tuple[int, dict, int, str | None]:
    body = {
        "sessionKey": session_key,
        "prompt": prompt,
        "agentId": agent_id,
    }

    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    started = time.monotonic()

    req = request.Request(
        BRIDGE_AI_REPLY_URL,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        },
    )

    try:
        with request.urlopen(req, timeout=UPSTREAM_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            status = resp.status
            content_type = resp.headers.get("content-type")
    except error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        content_type = exc.headers.get("content-type")
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return 0, {"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}, elapsed_ms, None

    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = raw.decode("utf-8", errors="replace")

    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            payload = {"ok": False, "error": "upstream_non_object_json"}
    except Exception:
        payload = {"ok": False, "error": "upstream_non_json", "body_snippet": text[:300]}

    return status, payload, elapsed_ms, content_type


def try_parse_json_object(text: str) -> dict | None:
    if not isinstance(text, str):
        return None

    s = text.strip()

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        candidate = s[start:end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None

    return None


def extract_tracking_number(text: str) -> str | None:
    if not isinstance(text, str):
        return None

    matches = re.findall(r"\b[A-Z0-9][A-Z0-9\-]{7,34}\b", text.upper())
    if not matches:
        return None

    noisy = {"SYNTHETIC", "DIAGNOSTIC", "TRACKING", "CUSTOMER", "MESSAGE"}
    for m in matches:
        compact = m.replace("-", "")
        if compact not in noisy and any(ch.isdigit() for ch in compact):
            return compact

    return None


def normalize_fast_reply(reply_text: str, user_text: str) -> dict:
    obj = try_parse_json_object(reply_text)

    if obj is None:
        clean_reply = reply_text.strip()
        if not clean_reply:
            clean_reply = "Please provide your tracking number so I can help you check your parcel status."

        return {
            "reply": clean_reply,
            "intent": "other",
            "tracking_number": extract_tracking_number(user_text),
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }

    reply = obj.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        for key in ["answer", "text", "message", "content"]:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                reply = value.strip()
                break

    if not isinstance(reply, str) or not reply.strip():
        reply = "Please provide your tracking number so I can help you check your parcel status."

    intent = obj.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        intent = "other"

    tracking_number = obj.get("tracking_number")
    if not isinstance(tracking_number, str) or not tracking_number.strip():
        tracking_number = extract_tracking_number(user_text)

    handoff_required = obj.get("handoff_required")
    if not isinstance(handoff_required, bool):
        handoff_required = False

    handoff_reason = obj.get("handoff_reason")
    if not isinstance(handoff_reason, str) or not handoff_reason.strip():
        handoff_reason = None

    recommended_agent_action = obj.get("recommended_agent_action")
    if not isinstance(recommended_agent_action, str) or not recommended_agent_action.strip():
        recommended_agent_action = None

    return {
        "reply": reply.strip(),
        "intent": intent.strip(),
        "tracking_number": tracking_number,
        "handoff_required": handoff_required,
        "handoff_reason": handoff_reason,
        "recommended_agent_action": recommended_agent_action,
    }


def make_responses_payload(strict_fast_reply: dict, upstream_payload: dict, elapsed_ms: int, model: str) -> dict:
    rid = "resp_" + uuid.uuid4().hex
    output_text = json.dumps(strict_fast_reply, ensure_ascii=False, separators=(",", ":"))

    content_item = {
        "type": "output_text",
        "text": output_text,
        "annotations": [],
    }

    message_item = {
        "id": "msg_" + uuid.uuid4().hex,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [content_item],
    }

    return {
        "id": rid,
        "object": "response",
        "created": now_ts(),
        "status": "completed",
        "model": model,
        "output": [message_item],
        "output_text": output_text,
        "usage": upstream_payload.get("usage") if isinstance(upstream_payload.get("usage"), dict) else None,
        "metadata": {
            "bridge": "nexus-openclaw-bridge-responses-proxy",
            "version": VERSION,
            "upstream_status": upstream_payload.get("status"),
            "upstream_ok": upstream_payload.get("ok"),
            "elapsed_ms": elapsed_ms,
            "bridgeRequestId": upstream_payload.get("bridgeRequestId"),
            "effectiveSessionKey": upstream_payload.get("effectiveSessionKey"),
            "normalized_fast_reply": True,
        },
    }


def safe_upstream_summary(upstream_payload: dict) -> dict:
    summary = {
        "ok": upstream_payload.get("ok"),
        "status": upstream_payload.get("status"),
        "error": upstream_payload.get("error"),
        "keys": sorted(upstream_payload.keys()),
        "bridgeRequestId_present": bool(upstream_payload.get("bridgeRequestId")),
        "effectiveSessionKey": upstream_payload.get("effectiveSessionKey"),
        "elapsedMs": upstream_payload.get("elapsedMs"),
        "timeoutMs": upstream_payload.get("timeoutMs"),
    }
    messages = upstream_payload.get("messages")
    if isinstance(messages, list):
        summary["messages_count"] = len(messages)
    return summary


class Handler(BaseHTTPRequestHandler):
    server_version = "NexusOpenClawBridgeResponsesProxy/" + VERSION

    def log_message(self, fmt: str, *args) -> None:
        safe_log(self, fmt % args)

    def do_GET(self) -> None:
        if self.path in ["/healthz", "/readyz"]:
            json_response(self, 200, {
                "ok": True,
                "service": "nexus-openclaw-bridge-responses-proxy",
                "bind": BIND_HOST,
                "port": PORT,
                "bridge_ai_reply_url": BRIDGE_AI_REPLY_URL,
                "token_file_exists": bool(load_token()),
                "version": VERSION,
            })
            return

        text_response(self, 404, "not found")

    def do_POST(self) -> None:
        if self.path not in ["/v1/responses", "/responses"]:
            text_response(self, 404, "not found")
            return

        if not check_auth(self):
            return

        payload, err = read_json_body(self)
        if err:
            json_response(self, 400, {"ok": False, "error": err})
            return

        assert payload is not None

        user_text = extract_user_text(payload)
        if not user_text:
            json_response(self, 400, {"ok": False, "error": "input_text_not_found"})
            return

        caller_instructions = extract_instructions(payload)
        upstream_prompt = build_upstream_prompt(caller_instructions=caller_instructions, user_text=user_text)

        session_key = extract_session_key(self, payload)
        agent_id = extract_agent_id(payload)
        request_id = self.headers.get("X-Request-Id") or ("proxy-" + uuid.uuid4().hex)
        model = str(payload.get("model") or f"openclaw:{agent_id}")

        status, upstream_payload, elapsed_ms, content_type = call_upstream(
            session_key=session_key,
            prompt=upstream_prompt,
            agent_id=agent_id,
            request_id=request_id,
        )

        if status != 200:
            json_response(self, 502, {
                "ok": False,
                "error": "upstream_http_error",
                "upstream_status": status,
                "upstream_content_type": content_type,
                "elapsed_ms": elapsed_ms,
                "upstream": safe_upstream_summary(upstream_payload),
            })
            return

        reply_text = extract_reply_text(upstream_payload)
        if not reply_text:
            json_response(self, 502, {
                "ok": False,
                "error": "bridge_empty_reply",
                "elapsed_ms": elapsed_ms,
                "upstream_content_type": content_type,
                "upstream": safe_upstream_summary(upstream_payload),
            })
            return

        strict_fast_reply = normalize_fast_reply(reply_text, user_text)
        json_response(self, 200, make_responses_payload(strict_fast_reply, upstream_payload, elapsed_ms, model))


def main() -> None:
    server = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    print(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service": "nexus-openclaw-bridge-responses-proxy",
        "version": VERSION,
        "bind": BIND_HOST,
        "port": PORT,
        "bridge_ai_reply_url": BRIDGE_AI_REPLY_URL,
        "token_file_exists": bool(load_token()),
    }, ensure_ascii=False), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
