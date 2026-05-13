from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from .webchat_fast_config import WebchatFastSettings, get_webchat_fast_settings
from .webchat_openclaw_stream_adapter import OpenClawResponsesStreamAdapter, NormalizedStreamEvent, StreamError

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


_STREAM_CLIENT: httpx.AsyncClient | None = None
_STREAM_CLIENT_KEY: tuple[str, int, int] | None = None

def _stream_client(settings: WebchatFastSettings) -> httpx.AsyncClient:
    global _STREAM_CLIENT, _STREAM_CLIENT_KEY
    key = (settings.openclaw_responses_stream_url, settings.openclaw_pool_max_connections, settings.openclaw_pool_max_keepalive)
    if _STREAM_CLIENT is None or _STREAM_CLIENT_KEY != key:
        timeout = httpx.Timeout(
            connect=settings.openclaw_stream_connect_timeout_ms / 1000,
            read=settings.openclaw_stream_read_timeout_ms / 1000,
            write=settings.openclaw_stream_connect_timeout_ms / 1000,
            pool=settings.openclaw_stream_connect_timeout_ms / 1000,
        )
        limits = httpx.Limits(
            max_connections=settings.openclaw_pool_max_connections,
            max_keepalive_connections=settings.openclaw_pool_max_keepalive,
        )
        _STREAM_CLIENT = httpx.AsyncClient(timeout=timeout, limits=limits)
        _STREAM_CLIENT_KEY = key
    return _STREAM_CLIENT

def _validate_stream_ready(settings: WebchatFastSettings) -> str:
    if not settings.enabled:
        raise OpenClawResponsesError("webchat fast AI is disabled")
    if not settings.stream_enabled:
        raise OpenClawResponsesError("stream is disabled")
    if not settings.openclaw_responses_stream_url:
        raise OpenClawResponsesError("OPENCLAW_RESPONSES_STREAM_URL is not configured")
    token = settings.stream_token
    if not token:
        raise OpenClawResponsesError("OpenClaw responses stream token is not configured")
    return token

def build_responses_request_body(*, instructions: str, input_text: str, settings: WebchatFastSettings | None = None, stream: bool = False) -> dict[str, Any]:
    settings = settings or get_webchat_fast_settings()
    return {
        "model": f"openclaw:{settings.openclaw_responses_agent_id}",
        "max_output_tokens": 350,
        "stream": bool(stream),
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


def _headers(*, token: str, session_key: str, request_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_key,
    }
    if request_id:
        headers["X-Request-Id"] = request_id
    return headers


def _validate_ready(settings: WebchatFastSettings) -> str:
    if not settings.enabled:
        raise OpenClawResponsesError("webchat fast AI is disabled")
    if not settings.openclaw_responses_url:
        raise OpenClawResponsesError("OPENCLAW_RESPONSES_URL is not configured")
    token = settings.token
    if not token:
        raise OpenClawResponsesError("OpenClaw responses token is not configured")
    return token


async def call_openclaw_responses(
    *,
    session_key: str,
    instructions: str,
    input_text: str,
    request_id: str | None = None,
    settings: WebchatFastSettings | None = None,
) -> OpenClawResponsesResult:
    settings = settings or get_webchat_fast_settings()
    token = _validate_ready(settings)

    body = build_responses_request_body(instructions=instructions, input_text=input_text, settings=settings, stream=False)
    headers = _headers(token=token, session_key=session_key, request_id=request_id)

    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            _client(settings).post(settings.openclaw_responses_stream_url, headers=headers, content=json.dumps(body).encode("utf-8")),
            timeout=settings.openclaw_total_timeout_ms / 1000,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException, httpx.NetworkError) as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        LOGGER.warning(
            "webchat_openclaw_responses_unavailable",
            extra={"event_payload": {"request_id": request_id, "elapsed_ms": elapsed_ms, "url":  _safe_url_for_log(str(settings.openclaw_responses_stream_url)), "error_type": type(exc).__name__}},
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


async def call_openclaw_responses_stream(
    *,
    session_key: str,
    instructions: str,
    input_text: str,
    request_id: str | None = None,
    settings: WebchatFastSettings | None = None,
) -> AsyncIterator[NormalizedStreamEvent]:
    """Call OpenClaw /v1/responses with stream=true and yield normalized events.

    Raw provider lines are normalized here and are never exposed to API routes or
    browsers. Logs intentionally omit Authorization, raw customer payload, and
    full raw OpenClaw payload.
    """
    settings = settings or get_webchat_fast_settings()
    token = _validate_stream_ready(settings)
    adapter = OpenClawResponsesStreamAdapter()
    body = build_responses_request_body(instructions=instructions, input_text=input_text, settings=settings, stream=True)
    headers = _headers(token=token, session_key=session_key, request_id=request_id)
    started = time.monotonic()
    try:
        async with _stream_client(settings).stream(
            "POST",
            settings.openclaw_responses_stream_url,
            headers=headers,
            content=json.dumps(body).encode("utf-8"),
        ) as response:
            if response.status_code == 404:
                raise OpenClawResponsesError("OpenClaw responses endpoint is unavailable", status_code=response.status_code)
            if response.status_code in {401, 403}:
                raise OpenClawResponsesError("OpenClaw responses authentication failed", status_code=response.status_code)
            if response.status_code >= 400:
                raise OpenClawResponsesError(f"OpenClaw responses returned HTTP {response.status_code}", status_code=response.status_code)
            buffer = ""
            async for chunk in response.aiter_text():
                if not chunk:
                    continue
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    if block.lstrip().startswith(("event:", "data:")):
                        for event in adapter.feed_sse_block(block):
                            yield event
                    else:
                        for line in block.splitlines():
                            for event in adapter.feed_json_line(line):
                                yield event
            if buffer.strip():
                if buffer.lstrip().startswith(("event:", "data:")):
                    for event in adapter.feed_sse_block(buffer):
                        yield event
                else:
                    for line in buffer.splitlines():
                        for event in adapter.feed_json_line(line):
                            yield event
    except OpenClawResponsesError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError) as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        LOGGER.warning(
            "webchat_openclaw_responses_stream_unavailable",
            extra={"event_payload": {"request_id": request_id, "elapsed_ms": elapsed_ms, "url":  _safe_url_for_log(str(settings.openclaw_responses_stream_url)), "error_type": type(exc).__name__}},
        )
        yield StreamError(error_code="stream_transport_error", message=None)
