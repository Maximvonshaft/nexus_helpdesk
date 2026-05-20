#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


@dataclass(frozen=True)
class TransportBoundarySettings:
    app_server_base_url: str | None
    timeout_ms: int = 15000
    allow_public_url: bool = False


@dataclass(frozen=True)
class TransportBoundaryResult:
    ok: bool
    status_code: int | None
    elapsed_ms: int
    response_json: dict[str, Any] | None
    safe_summary: dict[str, Any]
    error_code: str | None = None


def _private_or_tailnet_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    tailnet_or_cgnat = ipaddress.ip_network("100.64.0.0/10")
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip in tailnet_or_cgnat)


def _resolve_host_to_ips(host: str, port: int) -> tuple[set[ipaddress._BaseAddress] | None, str | None]:
    try:
        return {ipaddress.ip_address(host)}, None
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return {ipaddress.ip_address(item[4][0]) for item in infos}, None
    except Exception:
        return None, "app_server_host_unresolvable"


def validate_private_app_server_url(base_url: str | None, *, allow_public_url: bool = False) -> tuple[str | None, str | None]:
    value = (base_url or "").strip()
    if not value:
        return None, "app_server_base_url_missing"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, "app_server_base_url_invalid"
    if parsed.username or parsed.password:
        return None, "app_server_base_url_userinfo_forbidden"
    if allow_public_url:
        return value.rstrip("/"), None

    host = parsed.hostname
    resolved_ips, resolve_error = _resolve_host_to_ips(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    if resolve_error:
        return None, resolve_error
    if not resolved_ips or not any(_private_or_tailnet_ip(str(ip)) for ip in resolved_ips):
        return None, "app_server_url_must_be_private"
    return value.rstrip("/"), None


def _safe_summary(
    *,
    ok: bool,
    status_code: int | None,
    elapsed_ms: int,
    endpoint_path: str,
    error_code: str | None,
    response_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_keys = sorted(response_json.keys()) if isinstance(response_json, dict) else []
    return {
        "transport": "openclaw_codex_app_server",
        "endpoint_path": endpoint_path,
        "ok": ok,
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "response_type": "dict" if isinstance(response_json, dict) else None,
        "response_keys": response_keys[:20],
        "error_code": error_code,
    }


def _headers() -> dict[str, str]:
    return {
        "content-type": "application/json",
        "accept": "application/json",
        "x-nexus-transport-boundary": "codex-upstream-v1",
    }


async def post_account_login_start(
    *,
    settings: TransportBoundarySettings,
    login_payload: dict[str, Any],
) -> TransportBoundaryResult:
    start = time.monotonic()
    base_url, config_error = validate_private_app_server_url(
        settings.app_server_base_url,
        allow_public_url=settings.allow_public_url,
    )
    if config_error:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return TransportBoundaryResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_json=None,
            safe_summary=_safe_summary(ok=False, status_code=None, elapsed_ms=elapsed_ms, endpoint_path="account/login/start", error_code=config_error),
            error_code=config_error,
        )
    if not isinstance(login_payload, dict) or login_payload.get("type") not in {"chatgptAuthTokens", "apiKey"}:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return TransportBoundaryResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_json=None,
            safe_summary=_safe_summary(ok=False, status_code=None, elapsed_ms=elapsed_ms, endpoint_path="account/login/start", error_code="login_payload_invalid"),
            error_code="login_payload_invalid",
        )
    endpoint = urljoin(base_url + "/", "account/login/start")
    timeout_seconds = max(0.5, min(float(settings.timeout_ms) / 1000.0, 30.0))
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.post(endpoint, headers=_headers(), json=login_payload)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        status_code = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = None
        ok = 200 <= status_code < 300 and isinstance(payload, dict)
        error_code = None if ok else "app_server_login_http_error" if not (200 <= status_code < 300) else "app_server_login_invalid_json"
        return TransportBoundaryResult(
            ok=ok,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
            response_json=payload if isinstance(payload, dict) else None,
            safe_summary=_safe_summary(
                ok=ok,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                endpoint_path="account/login/start",
                error_code=error_code,
                response_json=payload if isinstance(payload, dict) else None,
            ),
            error_code=error_code,
        )
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return TransportBoundaryResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_json=None,
            safe_summary=_safe_summary(ok=False, status_code=None, elapsed_ms=elapsed_ms, endpoint_path="account/login/start", error_code="app_server_login_timeout"),
            error_code="app_server_login_timeout",
        )
    except httpx.RequestError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return TransportBoundaryResult(
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            response_json=None,
            safe_summary=_safe_summary(ok=False, status_code=None, elapsed_ms=elapsed_ms, endpoint_path="account/login/start", error_code="app_server_login_unavailable"),
            error_code="app_server_login_unavailable",
        )
