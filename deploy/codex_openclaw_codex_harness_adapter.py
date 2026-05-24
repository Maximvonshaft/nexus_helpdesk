#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "18800"))
OPENCLAW_ENABLED = os.environ.get("OPENCLAW_CODEX_RUNTIME_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
OPENCLAW_CLI = os.environ.get("OPENCLAW_CODEX_CLI", "openclaw").strip() or "openclaw"
AUTH_PROVIDER = os.environ.get("OPENCLAW_CODEX_AUTH_PROVIDER", "openai-codex").strip() or "openai-codex"
PLUGIN_PACKAGE = os.environ.get("OPENCLAW_CODEX_PLUGIN_PACKAGE", "@openclaw/codex").strip() or "@openclaw/codex"
REQUIRE_PLUGIN = os.environ.get("OPENCLAW_CODEX_REQUIRE_PLUGIN", "true").strip().lower() in {"1", "true", "yes", "on"}
MODEL = os.environ.get("OPENCLAW_CODEX_MODEL", "").strip()
INFER_TRANSPORT = os.environ.get("OPENCLAW_CODEX_INFER_TRANSPORT", "gateway").strip().lower() or "gateway"
AGENT_ID = os.environ.get("OPENCLAW_CODEX_AGENT", "").strip()
READY_TIMEOUT_SECONDS = float(os.environ.get("OPENCLAW_CODEX_READY_TIMEOUT_SECONDS", "5"))
REPLY_TIMEOUT_SECONDS = float(os.environ.get("OPENCLAW_CODEX_REPLY_TIMEOUT_SECONDS", "60"))
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
_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(access_token|refresh_token|client_secret|authorization)\s*[=:]\s*[^,\s]+", re.IGNORECASE),
]


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def redact(value: Any) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:1000]


def safe_log(handler: BaseHTTPRequestHandler, message: str) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": handler.client_address[0] if handler.client_address else None,
        "method": getattr(handler, "command", None),
        "path": getattr(handler, "path", None),
        "message": redact(message),
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


def cli_path() -> str | None:
    if not OPENCLAW_CLI:
        return None
    if os.path.isabs(OPENCLAW_CLI):
        return OPENCLAW_CLI if os.path.exists(OPENCLAW_CLI) else None
    return shutil.which(OPENCLAW_CLI)


def sanitized_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_TOKEN",
        "OPENAI_ACCESS_TOKEN",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "CODEX_REFRESH_TOKEN",
    ):
        env.pop(key, None)
    env["NO_COLOR"] = "1"
    env["CI"] = "1"
    return env


def run_openclaw(args: list[str], timeout_seconds: float, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    path = cli_path()
    if not path:
        raise FileNotFoundError("openclaw_cli_not_found")
    return subprocess.run(
        [path, *args],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=max(1.0, min(timeout_seconds, 120.0)),
        check=False,
        env=sanitized_env(),
        shell=False,
    )


def decode_json_output(result: subprocess.CompletedProcess[str]) -> Any:
    text = (result.stdout or "").strip()
    if not text:
        text = (result.stderr or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def iter_items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def normalized_plugin_values(plugin: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("id", "name", "package", "packageName", "npmPackage", "plugin", "slug"):
        value = plugin.get(key)
        if isinstance(value, str) and value.strip():
            values.add(value.strip().lower())
    provider_ids = plugin.get("providerIds") or plugin.get("provider_ids") or plugin.get("providers")
    if isinstance(provider_ids, list):
        for value in provider_ids:
            if isinstance(value, str) and value.strip():
                values.add(value.strip().lower())
    return values


def plugin_enabled(plugin: dict[str, Any]) -> bool:
    for key in ("enabled", "active", "loaded", "usable", "ready"):
        value = plugin.get(key)
        if isinstance(value, bool):
            return value
    status = plugin.get("status") or plugin.get("state")
    if isinstance(status, str):
        return status.strip().lower() in {"enabled", "active", "loaded", "ready", "ok"}
    return False


def plugin_payload_ready(payload: Any) -> bool:
    wanted = {PLUGIN_PACKAGE.lower(), "codex", AUTH_PROVIDER.lower()}
    for plugin in iter_items(payload, ("plugins", "items", "data")):
        if normalized_plugin_values(plugin) & wanted and plugin_enabled(plugin):
            return True
    return False


def profile_provider_matches(profile: dict[str, Any]) -> bool:
    for key in ("provider", "providerId", "provider_id", "id", "name"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip().lower() == AUTH_PROVIDER.lower():
            return True
    return False


def profile_usable(profile: dict[str, Any]) -> bool:
    for key in ("usable", "active", "authenticated", "authorized", "enabled", "ready"):
        value = profile.get(key)
        if isinstance(value, bool):
            return value
    status = profile.get("status") or profile.get("state")
    if isinstance(status, str):
        return status.strip().lower() in {"active", "authorized", "authenticated", "ready", "ok", "valid"}
    profile_type = profile.get("type") or profile.get("credentialType") or profile.get("credential_type")
    expires_at = profile.get("expiresAt") or profile.get("expires_at") or profile.get("expires")
    if isinstance(profile_type, str) and profile_type.strip().lower() == "oauth" and isinstance(expires_at, str):
        try:
            normalized = expires_at.strip()
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return parsed > datetime.now(timezone.utc)
    return False


def auth_payload_ready(payload: Any) -> bool:
    for profile in iter_items(payload, ("profiles", "items", "data", "accounts")):
        if profile_provider_matches(profile) and profile_usable(profile):
            return True
    return False


def plugin_ready() -> bool:
    if not REQUIRE_PLUGIN:
        return True
    try:
        result = run_openclaw(["plugins", "list", "--json"], READY_TIMEOUT_SECONDS)
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return plugin_payload_ready(decode_json_output(result))


def auth_ready() -> bool:
    args = ["models", "auth", "list", "--provider", AUTH_PROVIDER, "--json"]
    try:
        result = run_openclaw(args, READY_TIMEOUT_SECONDS)
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return auth_payload_ready(decode_json_output(result))


def model_configured() -> bool:
    return bool(MODEL and "/" in MODEL and not MODEL.lower().startswith(("stub/", "fixture/", "mock/")))


def health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "nexus-openclaw-codex-harness-adapter",
        "version": VERSION,
    }


def readiness_payload() -> dict[str, Any]:
    enabled = OPENCLAW_ENABLED
    path = cli_path()
    cli_configured = bool(path)
    plugin_configured = plugin_ready() if enabled and cli_configured else False
    auth_configured = auth_ready() if enabled and cli_configured else False
    model_ok = model_configured()
    transport_ok = INFER_TRANSPORT in {"local", "gateway"}
    ok = enabled and cli_configured and plugin_configured and auth_configured and model_ok and transport_ok
    reason = None
    if not enabled:
        reason = "openclaw_codex_runtime_disabled"
    elif not cli_configured:
        reason = "openclaw_cli_not_found"
    elif not plugin_configured:
        reason = "openclaw_codex_plugin_not_ready"
    elif not auth_configured:
        reason = "openclaw_codex_auth_not_ready"
    elif not model_ok:
        reason = "openclaw_codex_model_not_configured"
    elif not transport_ok:
        reason = "openclaw_codex_transport_invalid"
    return {
        "ok": ok,
        "service": "nexus-openclaw-codex-harness-adapter",
        "provider": "openclaw_codex",
        "adapter_stage": "p0_cli",
        "openclaw_cli_configured": cli_configured,
        "codex_plugin_package": PLUGIN_PACKAGE if REQUIRE_PLUGIN else None,
        "codex_plugin_ready": plugin_configured,
        "auth_provider": AUTH_PROVIDER,
        "auth_ready": auth_configured,
        "model_configured": model_ok,
        "model": MODEL if model_ok else None,
        "infer_transport": INFER_TRANSPORT if transport_ok else "invalid",
        "capabilities": {
            "strict_fast_reply_json": True,
            "reply_only": True,
            "official_openclaw_cli": True,
            "browser_cookie_scraping": False,
            "chatgpt_session_scraping": False,
            "shell_execution": False,
            "tool_execution": False,
            "direct_ticket_action": False,
            "direct_order_action": False,
            "direct_customer_write": False,
            "hardcoded_nonce_echo": False,
            "fixture_response": False,
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
        raise ValueError("openclaw_codex_reply_response_must_be_object")
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
        raise ValueError("openclaw_codex_reply_missing_required_fields")
    if not isinstance(payload.get("reply"), str) or not payload.get("reply", "").strip():
        raise ValueError("openclaw_codex_reply_text_invalid")
    if not isinstance(payload.get("intent"), str) or payload["intent"] not in _ALLOWED_INTENTS:
        raise ValueError("openclaw_codex_reply_intent_invalid")
    if payload.get("tracking_number") is not None and not isinstance(payload.get("tracking_number"), str):
        raise ValueError("openclaw_codex_reply_tracking_number_invalid")
    if not isinstance(payload.get("handoff_required"), bool):
        raise ValueError("openclaw_codex_reply_handoff_required_invalid")
    if payload.get("handoff_reason") is not None and not isinstance(payload.get("handoff_reason"), str):
        raise ValueError("openclaw_codex_reply_handoff_reason_invalid")
    if payload.get("recommended_agent_action") is not None and not isinstance(payload.get("recommended_agent_action"), str):
        raise ValueError("openclaw_codex_reply_recommended_agent_action_invalid")
    return {
        "reply": payload["reply"].strip()[:1200],
        "intent": payload["intent"],
        "tracking_number": payload.get("tracking_number"),
        "handoff_required": payload["handoff_required"],
        "handoff_reason": payload.get("handoff_reason"),
        "recommended_agent_action": payload.get("recommended_agent_action"),
    }


def parse_json_object(text_value: str) -> Any:
    text = text_value.strip()
    if not text:
        raise ValueError("openclaw_codex_empty_model_output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError("openclaw_codex_invalid_json_output") from exc
        raise ValueError("openclaw_codex_invalid_json_output")


def extract_model_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    for key in ("output_text", "response_text", "text", "reply", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    outputs = payload.get("outputs")
    if isinstance(outputs, list):
        parts: list[str] = []
        for item in outputs:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "output_text", "response_text"):
                    value = item.get(key)
                    if isinstance(value, str):
                        parts.append(value)
        if parts:
            return "\n".join(parts)
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    return ""


def build_prompt(payload: dict[str, Any]) -> str:
    prompt_payload = {
        "body": payload.get("body"),
        "messages": payload.get("messages") or [],
        "contract": payload.get("contract") or "speedaf_webchat_fast_reply_v1",
        "tracking_fact_summary": payload.get("tracking_fact_summary"),
        "tracking_fact_evidence_present": bool(payload.get("tracking_fact_evidence_present")),
        "chatgptAccountId": payload.get("chatgptAccountId"),
        "chatgptPlanType": payload.get("chatgptPlanType"),
        "response_contract": payload.get("response_contract") or {
            "reply": "string",
            "intent": "greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other",
            "tracking_number": "string|null",
            "handoff_required": "boolean",
            "handoff_reason": "string|null",
            "recommended_agent_action": "string|null",
        },
    }
    return (
        "You are the Nexus Speedaf WebChat Fast Reply engine running through the official OpenClaw Codex runtime.\n"
        "Return only strict JSON with keys reply, intent, tracking_number, handoff_required, handoff_reason, recommended_agent_action.\n"
        "Do not perform browser cookie scraping, ChatGPT session scraping, shell/tool execution, or ticket/order/customer writes.\n"
        "Do not expose tokens or internal OpenClaw/Codex details. Do not invent parcel status without trusted tracking evidence.\n"
        "If this is an admin nonce smoke, include the supplied nonce in the reply only because the model generated it from this prompt.\n\n"
        f"Input JSON:\n{json.dumps(prompt_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def infer_args(prompt: str) -> list[str]:
    args = ["infer", "model", "run"]
    if INFER_TRANSPORT == "gateway":
        args.append("--gateway")
    else:
        args.append("--local")
    if AGENT_ID:
        args.extend(["--agent", AGENT_ID])
    args.extend(["--model", MODEL, "--prompt", prompt, "--json"])
    return args


def call_openclaw_codex(payload: dict[str, Any]) -> dict[str, Any]:
    result = run_openclaw(infer_args(build_prompt(payload)), REPLY_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError("openclaw_codex_infer_failed")
    output = (result.stdout or "").strip()
    try:
        envelope = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError("openclaw_codex_invalid_json_output") from exc
    reply_text = extract_model_text(envelope)
    return strict_reply(parse_json_object(reply_text))


class Handler(BaseHTTPRequestHandler):
    server_version = "NexusOpenClawCodexHarnessAdapter/" + VERSION

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
        if not bearer_token(self):
            json_response(self, 401, {"ok": False, "error": "oauth_bearer_required"})
            return
        payload, err = read_json(self)
        if err:
            json_response(self, 400, {"ok": False, "error": err})
            return
        assert payload is not None
        ready = readiness_payload()
        if not ready["ok"]:
            json_response(self, 503, {"ok": False, "error": ready.get("reason") or "openclaw_codex_runtime_not_ready"})
            return
        try:
            validate_request_payload(payload)
            json_response(self, 200, call_openclaw_codex(payload))
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"ok": False, "error": "openclaw_codex_timeout"})
        except ValueError as exc:
            json_response(self, 502, {"ok": False, "error": str(exc)[:120]})
        except Exception:
            json_response(self, 502, {"ok": False, "error": "openclaw_codex_provider_call_failed"})


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
