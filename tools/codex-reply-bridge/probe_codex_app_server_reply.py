#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.webchat_fast_output_parser import (
    FastReplyParseError,
    UnexpectedToolCallError,
    parse_openclaw_fast_reply,
)

SECRET_PATTERNS = [
    re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"(CODEX_REPLY_BRIDGE_TOKEN|CODEX_APP_SERVER_TOKEN|CODEX_AUTH_TOKEN|OPENAI_API_KEY|CODEX_API_KEY|access_token|refresh_token|token)\s*[=:]\s*[^,\s]+",
        re.IGNORECASE,
    ),
    re.compile(r"auth\.json", re.IGNORECASE),
]

INTERNAL_OUTPUT_TERMS = [
    "OpenClaw",
    "Codex",
    "gateway",
    "localhost",
    "127.0.0.1",
    "Authorization",
    "Bearer",
    "api key",
    "secret",
    "access token",
    "refresh token",
]

DEFAULT_PAYLOAD = {
    "request_id": "codex-reply-probe-local",
    "tenant_key": "default",
    "channel_key": "website",
    "session_id": "codex-reply-probe-session",
    "body": "Hello, I want to check my parcel status.",
    "recent_context": [],
    "tracking_fact_summary": None,
    "tracking_fact_evidence_present": False,
    "strict_schema": "speedaf_webchat_fast_reply_v1",
}


def redact_secret_text(value: Any) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text[:8000]


def redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "secret", "password", "authorization", "api_key", "apikey", "refresh")):
                result[str(key)] = "[REDACTED_SECRET]"
            else:
                result[str(key)] = redact_json(item)
        return result
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def read_token_from_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()
    return token or None


def resolve_probe_url() -> str | None:
    return (
        os.getenv("CODEX_REPLY_BRIDGE_URL")
        or os.getenv("CODEX_APP_SERVER_BRIDGE_URL")
        or os.getenv("CODEX_APP_SERVER_REPLY_URL")
        or ""
    ).strip() or None


def validate_probe_url(url: str) -> tuple[bool, str | None]:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "probe_url_must_be_http_or_https"
    if parsed.username or parsed.password:
        return False, "probe_url_userinfo_forbidden"
    if not parsed.path or parsed.path == "/":
        return False, "probe_url_path_required"
    host = parsed.hostname.lower()
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "http" and host not in local_hosts:
        return False, "non_local_probe_url_must_use_https"
    return True, None


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_file:
        try:
            payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - probe must report, not crash without context.
            raise RuntimeError(f"payload_file_unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("payload_file_must_contain_json_object")
        return payload
    return dict(DEFAULT_PAYLOAD)


def post_json(url: str, payload: dict[str, Any], token: str | None, timeout_seconds: float) -> tuple[int, Any, str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Nexus-Probe": "codex-app-server-reply-v1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - URL is operator configured and guard-validated.
            body_text = response.read().decode("utf-8", errors="replace")
            return response.status, decode_response_body(body_text), None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        return exc.code, decode_response_body(body_text), "probe_http_error"
    except Exception as exc:  # noqa: BLE001 - probe must serialize deterministic diagnostics.
        return 0, {"error": redact_secret_text(str(exc))}, "probe_transport_error"


def decode_response_body(body_text: str) -> Any:
    text = body_text.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except ValueError:
        return text


def validate_fast_reply(payload: Any) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        parsed = parse_openclaw_fast_reply(payload)
    except UnexpectedToolCallError as exc:
        return False, None, getattr(exc, "error_code", "ai_unexpected_tool_call")
    except FastReplyParseError as exc:
        return False, None, getattr(exc, "error_code", "ai_invalid_output")
    return True, {
        "reply": parsed.reply,
        "intent": parsed.intent,
        "tracking_number": parsed.tracking_number,
        "handoff_required": parsed.handoff_required,
        "handoff_reason": parsed.handoff_reason,
        "recommended_agent_action": parsed.recommended_agent_action,
    }, None


def contains_internal_terms(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    lowered = text.lower()
    return any(term.lower() in lowered for term in INTERNAL_OUTPUT_TERMS)


def write_report(artifact_dir: Path, result: dict[str, Any]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    safe_result = redact_json(result)
    (artifact_dir / "raw_sanitized.json").write_text(
        json.dumps(safe_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verdict = str(safe_result.get("final_verdict") or "UNKNOWN")
    (artifact_dir / "final_verdict.txt").write_text(verdict + "\n", encoding="utf-8")
    lines = [
        "# Codex App-Server Reply Probe Report",
        "",
        f"- final_verdict: `{verdict}`",
        f"- auth_configured: `{safe_result.get('auth_configured')}`",
        f"- endpoint_configured: `{safe_result.get('endpoint_configured')}`",
        f"- http_status: `{safe_result.get('http_status')}`",
        f"- elapsed_ms: `{safe_result.get('elapsed_ms')}`",
        f"- parse_ok: `{safe_result.get('parse_ok')}`",
        f"- error_code: `{safe_result.get('error_code')}`",
        f"- secret_leak_check: `{safe_result.get('secret_leak_check')}`",
        f"- internal_term_check: `{safe_result.get('internal_term_check')}`",
        "",
        "## Safe summary",
        "",
        "```json",
        json.dumps(safe_result.get("safe_summary") or {}, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Boundary",
        "",
        "This probe only validates a configured private Codex reply bridge endpoint. It does not read browser cookies, scrape ChatGPT sessions, run shell commands through Codex, execute tools, write database rows, or send customer-visible outbound messages.",
        "",
    ]
    (artifact_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe a private Codex app-server reply bridge for Nexus strict JSON compatibility.")
    parser.add_argument("--payload-file", help="Optional JSON payload file to send instead of the built-in safe sample.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the probe cannot produce strict JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    artifact_dir = Path(os.getenv("CODEX_REPLY_PROBE_ARTIFACT_DIR") or "artifacts/codex_reply_probe")
    url = resolve_probe_url()
    token = read_token_from_file(os.getenv("CODEX_REPLY_BRIDGE_TOKEN_FILE") or os.getenv("CODEX_APP_SERVER_TOKEN_FILE"))
    token = token or (os.getenv("CODEX_REPLY_BRIDGE_TOKEN") or "").strip() or None
    timeout_ms = int(os.getenv("CODEX_REPLY_PROBE_TIMEOUT_MS") or os.getenv("CODEX_APP_SERVER_TIMEOUT_MS") or "15000")

    result: dict[str, Any] = {
        "provider": "codex_app_server_reply_probe",
        "endpoint_configured": bool(url),
        "auth_configured": bool(token),
        "http_status": None,
        "elapsed_ms": 0,
        "parse_ok": False,
        "error_code": None,
        "secret_leak_check": "not_evaluated",
        "internal_term_check": "not_evaluated",
        "safe_summary": {},
        "final_verdict": "UNKNOWN",
    }

    try:
        if not url:
            result.update(
                error_code="probe_url_missing",
                final_verdict="CONFIG_MISSING",
                safe_summary={"message": "Set CODEX_REPLY_BRIDGE_URL or CODEX_APP_SERVER_BRIDGE_URL to run the probe."},
            )
            return_code = 2 if args.strict else 0
            return return_code

        ok, guard_error = validate_probe_url(url)
        if not ok:
            result.update(error_code=guard_error, final_verdict="CONFIG_REJECTED", safe_summary={"endpoint": redact_secret_text(url)})
            return_code = 2 if args.strict else 0
            return return_code

        payload = load_payload(args)
        status, response_payload, transport_error = post_json(url, payload, token, timeout_ms / 1000)
        parse_ok, parsed_reply, parse_error = validate_fast_reply(response_payload)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        safe_response = redact_json(response_payload)
        response_text = json.dumps(safe_response, ensure_ascii=False, sort_keys=True)
        secret_leak = bool(token and token in response_text)
        internal_terms = contains_internal_terms(parsed_reply or safe_response)
        final_verdict = "PASS" if status and status < 400 and parse_ok and not secret_leak and not internal_terms else "FAIL"
        result.update(
            http_status=status,
            elapsed_ms=elapsed_ms,
            parse_ok=parse_ok,
            error_code=transport_error or parse_error,
            secret_leak_check="FAIL" if secret_leak else "PASS",
            internal_term_check="FAIL" if internal_terms else "PASS",
            safe_summary={
                "endpoint": redact_secret_text(url),
                "response_type": type(response_payload).__name__,
                "parsed_reply": parsed_reply,
            },
            final_verdict=final_verdict,
        )
        return 0 if final_verdict == "PASS" or not args.strict else 1
    except Exception as exc:  # noqa: BLE001 - probe must always write artifacts.
        result.update(
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_code="probe_exception",
            safe_summary={"exception": redact_secret_text(str(exc))},
            final_verdict="FAIL",
        )
        return 1 if args.strict else 0
    finally:
        result["elapsed_ms"] = result.get("elapsed_ms") or int((time.monotonic() - started) * 1000)
        write_report(artifact_dir, result)
        print(json.dumps(redact_json(result), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
