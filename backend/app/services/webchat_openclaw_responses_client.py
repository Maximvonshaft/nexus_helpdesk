from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .webchat_fast_config import WebchatFastSettings, get_webchat_fast_settings

LOGGER = logging.getLogger("nexusdesk")


class OpenClawResponsesError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OpenClawResponsesResult:
    payload: dict[str, Any]
    elapsed_ms: int
    status_code: int


_CLIENT: httpx.AsyncClient | None = None
_CLIENT_KEY: tuple[str, int, int] | None = None


def _safe_url_for_log(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return f"{parsed.scheme}://{parsed.host}{parsed.path}"
    except Exception:
        return "configured_openclaw_responses_url"


def _client(settings: WebchatFastSettings) -> httpx.AsyncClient:
    global _CLIENT, _CLIENT_KEY
    key = (settings.openclaw_responses_url, settings.openclaw_pool_max_connections, settings.openclaw_pool_max_keepalive)
    if _CLIENT is None or _CLIENT_KEY != key:
        timeout = httpx.Timeout(
            connect=settings.openclaw_connect_timeout_ms / 1000,
            read=settings.openclaw_read_timeout_ms / 1000,
            write=settings.openclaw_connect_timeout_ms / 1000,
            pool=settings.openclaw_connect_timeout_ms / 1000,
        )
        limits = httpx.Limits(
            max_connections=settings.openclaw_pool_max_connections,
            max_keepalive_connections=settings.openclaw_pool_max_keepalive,
        )
        _CLIENT = httpx.AsyncClient(timeout=timeout, limits=limits)
        _CLIENT_KEY = key
    return _CLIENT


def build_responses_request_body(*, instructions: str, input_text: str, settings: WebchatFastSettings | None = None) -> dict[str, Any]:
    settings = settings or get_webchat_fast_settings()
    return {
        "model": f"openclaw:{settings.openclaw_responses_agent_id}",
        "max_output_tokens": 350,
        "stream": False,
        "instructions": instructions,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": input_text},
                ],
            }
        ],
    }


async def call_openclaw_responses(
    *,
    session_key: str,
    instructions: str,
    input_text: str,
    request_id: str | None = None,
    settings: WebchatFastSettings | None = None,
) -> OpenClawResponsesResult:
    settings = settings or get_webchat_fast_settings()
    if not settings.enabled:
        raise OpenClawResponsesError("webchat fast AI is disabled")
    if not settings.openclaw_responses_url:
        raise OpenClawResponsesError("OPENCLAW_RESPONSES_URL is not configured")
    token = settings.token
    if not token:
        raise OpenClawResponsesError("OpenClaw responses token is not configured")

    body = build_responses_request_body(instructions=instructions, input_text=input_text, settings=settings)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_key,
    }
    if request_id:
        headers["X-Request-Id"] = request_id

    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            _client(settings).post(settings.openclaw_responses_url, headers=headers, content=json.dumps(body).encode("utf-8")),
            timeout=settings.openclaw_total_timeout_ms / 1000,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException, httpx.NetworkError) as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        LOGGER.warning(
            "webchat_openclaw_responses_unavailable",
            extra={"event_payload": {"request_id": request_id, "elapsed_ms": elapsed_ms, "url": _safe_url_for_log(settings.openclaw_responses_url), "error_type": type(exc).__name__}},
        )
        raise OpenClawResponsesError("OpenClaw responses request failed") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if response.status_code == 404:
        raise OpenClawResponsesError("OpenClaw responses endpoint is unavailable", status_code=response.status_code)
    if response.status_code in {401, 403}:
        raise OpenClawResponsesError("OpenClaw responses authentication failed", status_code=response.status_code)
    if response.status_code >= 400:
        raise OpenClawResponsesError(f"OpenClaw responses returned HTTP {response.status_code}", status_code=response.status_code)

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenClawResponsesError("OpenClaw responses returned non-JSON response", status_code=response.status_code) from exc
    if not isinstance(payload, dict):
        raise OpenClawResponsesError("OpenClaw responses payload must be an object", status_code=response.status_code)
    return OpenClawResponsesResult(payload=payload, elapsed_ms=elapsed_ms, status_code=response.status_code)
