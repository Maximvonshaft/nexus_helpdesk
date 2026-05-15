from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..ai_runtime.openclaw_responses_provider import build_fast_reply_instructions
from ..ai_runtime.safety_contract import redact_secret_text, safe_endpoint_summary, safe_exception_message
from ..webchat_fast_output_parser import FastReplyParseError, parse_openclaw_fast_reply
from .schemas import CodexTokenProbeResult


def _app_env() -> str:
    return os.getenv("APP_ENV", "development").strip().lower() or "development"


def _read_secret_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    try:
        value = Path(path_value).read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    return value or None


def _read_codex_token() -> tuple[str | None, str | None]:
    file_token = _read_secret_file(os.getenv("CODEX_AUTH_TOKEN_FILE"))
    if file_token:
        return file_token, "CODEX_AUTH_TOKEN_FILE"
    raw = (os.getenv("CODEX_AUTH_TOKEN") or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw.split(None, 1)[1].strip()
    if raw:
        if _app_env() not in {"development", "test", "local"}:
            return None, "production_plaintext_token_forbidden"
        return raw, "CODEX_AUTH_TOKEN"
    return None, None


def _probe_prompt() -> str:
    return (
        "Return only strict JSON for a Speedaf WebChat customer service greeting. "
        "Do not mention internal systems. "
        "Use this exact schema: "
        '{"reply":"customer visible AI reply","intent":"greeting","tracking_number":null,'
        '"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'
    )


def _request_body() -> dict[str, Any]:
    return {
        "model": os.getenv("CODEX_AUTH_PROBE_MODEL", "codex-auth-probe"),
        "stream": False,
        "instructions": build_fast_reply_instructions(),
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": _probe_prompt()}],
            }
        ],
    }


def _not_confirmed(started: float, *, token_source: str | None) -> CodexTokenProbeResult:
    return CodexTokenProbeResult(
        ok=False,
        provider="codex_auth_probe",
        transport="not_confirmed",
        elapsed_ms=int((time.monotonic() - started) * 1000),
        parse_ok=False,
        error_code="transport_not_confirmed",
        safe_error=(
            "CODEX_AUTH_PROBE_URL is not configured. Phase 0 did not assume that a "
            "Codex/ChatGPT token can be used as a normal OpenAI API key."
        ),
        raw_payload_safe_summary={"token_source": token_source, "endpoint": None},
    )


async def run_codex_token_probe() -> CodexTokenProbeResult:
    started = time.monotonic()
    token, source = _read_codex_token()
    if source == "production_plaintext_token_forbidden":
        return CodexTokenProbeResult(
            ok=False,
            provider="codex_auth_probe",
            transport="not_configured",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            parse_ok=False,
            error_code="production_plaintext_token_forbidden",
            safe_error="CODEX_AUTH_TOKEN is forbidden outside development/test/local. Use CODEX_AUTH_TOKEN_FILE.",
        )
    if not token:
        return CodexTokenProbeResult(
            ok=False,
            provider="codex_auth_probe",
            transport="not_configured",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            parse_ok=False,
            error_code="codex_auth_token_missing",
            safe_error="CODEX_AUTH_TOKEN_FILE is not configured and CODEX_AUTH_TOKEN is not allowed or empty.",
        )

    probe_url = (os.getenv("CODEX_AUTH_PROBE_URL") or "").strip()
    if not probe_url:
        return _not_confirmed(started, token_source=source)

    timeout_ms = int(os.getenv("CODEX_AUTH_PROBE_TIMEOUT_MS", "15000"))
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            response = await client.post(probe_url, headers=headers, content=json.dumps(_request_body()).encode("utf-8"))
    except Exception as exc:
        return CodexTokenProbeResult(
            ok=False,
            provider="codex_auth_probe",
            transport=safe_endpoint_summary(probe_url) or "configured_endpoint",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            parse_ok=False,
            error_code="probe_transport_error",
            safe_error=safe_exception_message(exc),
            raw_payload_safe_summary={"endpoint": safe_endpoint_summary(probe_url), "token_source": source},
        )

    try:
        payload = response.json()
    except ValueError as exc:
        return CodexTokenProbeResult(
            ok=False,
            provider="codex_auth_probe",
            transport=safe_endpoint_summary(probe_url) or "configured_endpoint",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            parse_ok=False,
            error_code="probe_non_json_response",
            safe_error=safe_exception_message(exc),
            raw_payload_safe_summary={"status_code": response.status_code, "endpoint": safe_endpoint_summary(probe_url)},
        )

    if response.status_code >= 400:
        return CodexTokenProbeResult(
            ok=False,
            provider="codex_auth_probe",
            transport=safe_endpoint_summary(probe_url) or "configured_endpoint",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            parse_ok=False,
            error_code="probe_http_error",
            safe_error=f"Probe endpoint returned HTTP {response.status_code}",
            raw_payload_safe_summary={"status_code": response.status_code, "endpoint": safe_endpoint_summary(probe_url)},
        )

    try:
        parse_openclaw_fast_reply(payload)
    except FastReplyParseError as exc:
        return CodexTokenProbeResult(
            ok=False,
            provider="codex_auth_probe",
            transport=safe_endpoint_summary(probe_url) or "configured_endpoint",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            parse_ok=False,
            error_code=getattr(exc, "error_code", "ai_invalid_output"),
            safe_error=safe_exception_message(exc),
            raw_payload_safe_summary={"status_code": response.status_code, "endpoint": safe_endpoint_summary(probe_url)},
        )

    return CodexTokenProbeResult(
        ok=True,
        provider="codex_auth_probe",
        transport=safe_endpoint_summary(probe_url) or "configured_endpoint",
        elapsed_ms=int((time.monotonic() - started) * 1000),
        parse_ok=True,
        error_code=None,
        safe_error=None,
        raw_payload_safe_summary={"status_code": response.status_code, "endpoint": safe_endpoint_summary(probe_url)},
    )


def main() -> None:
    result = asyncio.run(run_codex_token_probe())
    print(redact_secret_text(json.dumps(result.to_dict(), ensure_ascii=False)))


if __name__ == "__main__":
    main()
