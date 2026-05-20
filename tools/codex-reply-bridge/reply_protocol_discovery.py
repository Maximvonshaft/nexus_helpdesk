#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from upstream_transport_boundary import validate_private_app_server_url


DEFAULT_CANDIDATE_PATHS = [
    "/healthz",
    "/readyz",
    "/openapi.json",
    "/docs",
    "/account/status",
    "/conversation",
    "/conversation/start",
    "/conversation/turn",
    "/conversation/reply",
    "/reply",
    "/turn",
    "/chat",
    "/messages",
    "/responses",
]

SAFE_GET_METHODS = ("OPTIONS", "GET")
POST_METHOD = "POST"


@dataclass(frozen=True)
class ProbeSettings:
    base_url: str | None
    candidate_paths: list[str]
    timeout_ms: int = 5000
    allow_public_url: bool = False
    allow_post_probe: bool = False


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    base_url_accepted: bool
    error_code: str | None
    results: list[dict[str, Any]]
    elapsed_ms: int


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def parse_candidate_paths(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_CANDIDATE_PATHS)
    paths: list[str] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        if not value.startswith("/"):
            value = "/" + value
        if value not in paths:
            paths.append(value)
    return paths or list(DEFAULT_CANDIDATE_PATHS)


def _headers() -> dict[str, str]:
    return {
        "accept": "application/json, text/plain;q=0.5, */*;q=0.1",
        "content-type": "application/json",
        "x-nexus-protocol-discovery": "codex-reply-v1",
    }


def synthetic_post_payload(path: str) -> dict[str, Any]:
    return {
        "probe": True,
        "request_id": "protocol-discovery-probe",
        "session_id": "protocol-discovery-session",
        "path_hint": path,
        "body": "Synthetic protocol discovery probe. Do not treat as a customer message.",
        "recent_context": [],
        "strict_schema": "speedaf_webchat_fast_reply_v1",
    }


def _content_type(headers: httpx.Headers) -> str | None:
    value = headers.get("content-type")
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def _response_keys(response: httpx.Response) -> list[str]:
    try:
        payload = response.json()
    except ValueError:
        return []
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())[:30]
    return []


def _result_summary(*, method: str, path: str, status_code: int | None, elapsed_ms: int, error_code: str | None, response: httpx.Response | None = None) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "content_type": _content_type(response.headers) if response is not None else None,
        "allow_header_present": bool(response.headers.get("allow")) if response is not None else False,
        "allow_header": response.headers.get("allow") if response is not None else None,
        "response_keys": _response_keys(response) if response is not None else [],
        "body_bytes": len(response.content) if response is not None else None,
        "error_code": error_code,
    }


async def _probe_one(client: httpx.AsyncClient, *, base_url: str, method: str, path: str) -> dict[str, Any]:
    start = time.monotonic()
    endpoint = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    try:
        if method == POST_METHOD:
            response = await client.post(endpoint, headers=_headers(), json=synthetic_post_payload(path))
        else:
            response = await client.request(method, endpoint, headers=_headers())
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return _result_summary(method=method, path=path, status_code=response.status_code, elapsed_ms=elapsed_ms, error_code=None, response=response)
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return _result_summary(method=method, path=path, status_code=None, elapsed_ms=elapsed_ms, error_code="protocol_probe_timeout")
    except httpx.RequestError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return _result_summary(method=method, path=path, status_code=None, elapsed_ms=elapsed_ms, error_code="protocol_probe_unavailable")


async def discover_reply_protocol(settings: ProbeSettings) -> ProbeResult:
    start = time.monotonic()
    base_url, error_code = validate_private_app_server_url(settings.base_url, allow_public_url=settings.allow_public_url)
    if error_code:
        return ProbeResult(ok=False, base_url_accepted=False, error_code=error_code, results=[], elapsed_ms=int((time.monotonic() - start) * 1000))

    methods = list(SAFE_GET_METHODS)
    if settings.allow_post_probe:
        methods.append(POST_METHOD)

    timeout_seconds = max(0.5, min(float(settings.timeout_ms) / 1000.0, 30.0))
    probe_results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        for path in settings.candidate_paths:
            for method in methods:
                probe_results.append(await _probe_one(client, base_url=base_url or "", method=method, path=path))

    ok = any(item.get("status_code") in {200, 201, 202, 204, 400, 401, 403, 404, 405, 422} for item in probe_results)
    return ProbeResult(ok=ok, base_url_accepted=True, error_code=None, results=probe_results, elapsed_ms=int((time.monotonic() - start) * 1000))


def result_to_safe_dict(result: ProbeResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "base_url_accepted": result.base_url_accepted,
        "error_code": result.error_code,
        "elapsed_ms": result.elapsed_ms,
        "result_count": len(result.results),
        "results": result.results,
        "boundary": {
            "credential_material_sent": False,
            "customer_message_sent": False,
            "post_probe_enabled": any(item.get("method") == POST_METHOD for item in result.results),
            "browser_cookie_scraping": False,
            "chatgpt_session_scraping": False,
            "shell_execution": False,
            "tool_execution": False,
        },
    }


def settings_from_env() -> ProbeSettings:
    return ProbeSettings(
        base_url=(os.getenv("CODEX_REPLY_PROTOCOL_BASE_URL") or os.getenv("CODEX_UPSTREAM_APP_SERVER_BASE_URL") or "").strip() or None,
        candidate_paths=parse_candidate_paths(os.getenv("CODEX_REPLY_PROTOCOL_CANDIDATE_PATHS")),
        timeout_ms=_env_int("CODEX_REPLY_PROTOCOL_TIMEOUT_MS", 5000, minimum=500, maximum=30000),
        allow_public_url=_env_bool("CODEX_REPLY_PROTOCOL_ALLOW_PUBLIC_URL", False),
        allow_post_probe=_env_bool("CODEX_REPLY_PROTOCOL_ALLOW_POST_PROBE", False),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe private Codex app-server reply protocol candidates safely.")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--candidate-paths", default=None, help="Comma-separated path list. Defaults to safe built-in candidates.")
    parser.add_argument("--timeout-ms", type=int, default=None)
    parser.add_argument("--allow-public-url", action="store_true")
    parser.add_argument("--allow-post-probe", action="store_true")
    args = parser.parse_args()

    env_settings = settings_from_env()
    settings = ProbeSettings(
        base_url=args.base_url or env_settings.base_url,
        candidate_paths=parse_candidate_paths(args.candidate_paths) if args.candidate_paths is not None else env_settings.candidate_paths,
        timeout_ms=args.timeout_ms if args.timeout_ms is not None else env_settings.timeout_ms,
        allow_public_url=bool(args.allow_public_url or env_settings.allow_public_url),
        allow_post_probe=bool(args.allow_post_probe or env_settings.allow_post_probe),
    )
    result = asyncio.run(discover_reply_protocol(settings))
    print(json.dumps(result_to_safe_dict(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.base_url_accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
