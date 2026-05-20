#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from upstream_transport_boundary import validate_private_app_server_url


@dataclass(frozen=True)
class ReplyTransportSettings:
    app_server_base_url: str | None
    reply_path: str = "/reply"
    timeout_ms: int = 15000
    allow_public_url: bool = False
    bearer_token: str | None = None


@dataclass(frozen=True)
class ReplyTransportResult:
    ok: bool
    status_code: int | None
    elapsed_ms: int
    response_payload: Any | None
    safe_summary: dict[str, Any]
    error_code: str | None = None


def normalize_reply_path(path_value: str | None) -> tuple[str | None, str | None]:
    value = (path_value or "").strip() or "/reply"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or parsed.username or parsed.password:
        return None, "reply_path_must_be_relative"
    if not value.startswith("/"):
        value = "/" + value
    if ".." in value.split("/"):
        return None, "reply_path_parent_segment_forbidden"
    return value, None


def _response_keys(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())[:30]
    return []


def _safe_summary(
    *,
    ok: bool,
    status_code: int | None,
    elapsed_ms: int,
    endpoint_path: str | None,
    error_code: str | None,
    response_payload: Any | None = None,
) -> dict[str, Any]:
    return {
        "transport": "codex_app_server_reply",
        "endpoint_path": endpoint_path,
        "ok": ok,
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "response_type": type(response_payload).__name__ if response_payload is not None else None,
        "response_keys": _response_keys(response_payload),
        "error_code": error_code,
    }


def _headers(settings: ReplyTransportSettings) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/plain;q=0.5",
        "x-nexus-provider-runtime": "codex-app-server-reply-v1",
    }
    if settings.bearer_token:
        headers["authorization"] = "Bearer " + settings.bearer_token
    return headers


async def post_reply_turn(
    *,
    settings: ReplyTransportSettings,
    reply_payload: dict[str, Any],
) -> ReplyTransportResult:
    start = time.monotonic()
    base_url, config_error = validate_private_app_server_url(
        settings.app_server_base_url,
        allow_public_url=settings.allow_public_url,
    )
    reply_path, path_error = normalize_reply_path(settings.reply_path)
    endpoint_path = reply_path or settings.reply_path
    if config_error or path_error:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        error_code = config_error or path_error or "reply_transport_config_invalid"
        return ReplyTransportResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_payload=None,
            safe_summary=_safe_summary(
                ok=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                endpoint_path=endpoint_path,
                error_code=error_code,
            ),
            error_code=error_code,
        )
    if not isinstance(reply_payload, dict) or not reply_payload.get("body"):
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ReplyTransportResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_payload=None,
            safe_summary=_safe_summary(
                ok=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                endpoint_path=endpoint_path,
                error_code="reply_payload_invalid",
            ),
            error_code="reply_payload_invalid",
        )

    endpoint = urljoin((base_url or "").rstrip("/") + "/", (reply_path or "/reply").lstrip("/"))
    timeout_seconds = max(0.5, min(float(settings.timeout_ms) / 1000.0, 30.0))
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.post(endpoint, headers=_headers(settings), json=reply_payload)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        status_code = response.status_code
        text = response.text.strip()
        payload: Any | None
        try:
            payload = response.json() if text else ""
        except ValueError:
            payload = text
        ok = 200 <= status_code < 300
        error_code = None if ok else "app_server_reply_http_error"
        return ReplyTransportResult(
            ok=ok,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
            response_payload=payload,
            safe_summary=_safe_summary(
                ok=ok,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                endpoint_path=reply_path,
                error_code=error_code,
                response_payload=payload,
            ),
            error_code=error_code,
        )
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ReplyTransportResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_payload=None,
            safe_summary=_safe_summary(
                ok=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                endpoint_path=endpoint_path,
                error_code="app_server_reply_timeout",
            ),
            error_code="app_server_reply_timeout",
        )
    except httpx.RequestError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ReplyTransportResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_payload=None,
            safe_summary=_safe_summary(
                ok=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                endpoint_path=endpoint_path,
                error_code="app_server_reply_unavailable",
            ),
            error_code="app_server_reply_unavailable",
        )
