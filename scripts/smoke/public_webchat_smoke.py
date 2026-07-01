#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_ALLOWED_REPLY_SOURCES = {
    "private_ai_runtime",
    "codex_app_server",
    "openai_responses",
    "codex_direct",
}


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes
    elapsed_ms: int

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        return json.loads(self.text())


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    payload: object | None = None,
    timeout: float = 20.0,
    allow_http_error: bool = False,
) -> HttpResult:
    data: bytes | None = None
    final_headers = {
        "User-Agent": os.getenv("WEBCHAT_SMOKE_USER_AGENT", "curl/8.0"),
        "Accept": "*/*",
    }
    final_headers.update(headers or {})
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    started = time.monotonic()
    req = Request(url, data=data, headers=final_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return HttpResult(resp.status, dict(resp.headers.items()), body, elapsed_ms)
    except HTTPError as exc:
        body = exc.read()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if allow_http_error:
            return HttpResult(exc.code, dict(exc.headers.items()), body, elapsed_ms)
        raise RuntimeError(f"{method} {url} returned HTTP {exc.code}: {body[:300]!r}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def write_bytes(out_dir: Path, name: str, body: bytes) -> Path:
    path = out_dir / name
    path.write_bytes(body)
    return path


def write_json(out_dir: Path, name: str, payload: object) -> Path:
    path = out_dir / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def fail(errors: list[str]) -> None:
    if errors:
        raise SystemExit("\n".join(errors))


def normalized_header(headers: Mapping[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def parse_script_urls(base_url: str, html: str) -> list[str]:
    urls: list[str] = []
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)", html, flags=re.IGNORECASE):
        url = urljoin(base_url.rstrip("/") + "/webchat/demo/", src)
        if url not in urls:
            urls.append(url)
    return urls


def assert_release_metadata(
    errors: list[str],
    name: str,
    payload: Mapping[str, object],
    *,
    expected_git_sha: str,
    expected_image_tag: str,
    require_complete: bool,
) -> None:
    if expected_git_sha and payload.get("git_sha") != expected_git_sha:
        errors.append(f"{name}_git_sha={payload.get('git_sha')} expected={expected_git_sha}")
    if expected_image_tag and payload.get("image_tag") != expected_image_tag:
        errors.append(f"{name}_image_tag={payload.get('image_tag')} expected={expected_image_tag}")
    if require_complete and payload.get("release_metadata_complete") is not True:
        errors.append(f"{name}_release_metadata_missing={payload.get('release_metadata_missing')}")


def run(args: argparse.Namespace) -> dict[str, object]:
    base_url = args.base_url.rstrip("/")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    healthz_result = request(f"{base_url}/healthz", timeout=args.timeout_seconds)
    readyz_result = request(f"{base_url}/readyz", timeout=args.timeout_seconds)
    write_bytes(out_dir, "healthz.json", healthz_result.body)
    write_bytes(out_dir, "readyz.json", readyz_result.body)
    healthz = healthz_result.json()
    readyz = readyz_result.json()
    if not isinstance(healthz, dict):
        errors.append("healthz_not_json_object")
        healthz = {}
    if not isinstance(readyz, dict):
        errors.append("readyz_not_json_object")
        readyz = {}

    if healthz.get("status") != "ok":
        errors.append(f"healthz_status={healthz.get('status')}")
    if readyz.get("status") != "ready":
        errors.append(f"readyz_status={readyz.get('status')}")
    if readyz.get("database") != "ok":
        errors.append(f"readyz_database={readyz.get('database')}")
    if not readyz.get("migration_revision"):
        errors.append("readyz_migration_revision_missing")

    assert_release_metadata(
        errors,
        "healthz",
        healthz,
        expected_git_sha=args.expected_git_sha,
        expected_image_tag=args.expected_image_tag,
        require_complete=args.require_release_metadata_complete,
    )
    assert_release_metadata(
        errors,
        "readyz",
        readyz,
        expected_git_sha=args.expected_git_sha,
        expected_image_tag=args.expected_image_tag,
        require_complete=args.require_release_metadata_complete,
    )

    demo_result = request(f"{base_url}/webchat/demo/", timeout=args.timeout_seconds)
    demo_html = demo_result.text()
    write_bytes(out_dir, "webchat_demo.html", demo_result.body)
    script_urls = parse_script_urls(base_url, demo_html)
    write_json(out_dir, "webchat_demo_scripts.json", script_urls)
    if demo_result.status != 200:
        errors.append(f"webchat_demo_status={demo_result.status}")
    if "webchat" not in demo_html.lower():
        errors.append("webchat_demo_marker_missing")
    if "/api/webchat/fast-reply" not in demo_html and "fast-reply" not in demo_html:
        if not script_urls:
            errors.append("webchat_demo_fast_reply_marker_missing")

    script_markers: dict[str, list[str]] = {}
    for index, script_url in enumerate(script_urls, start=1):
        script_result = request(script_url, timeout=args.timeout_seconds)
        body = script_result.text()
        write_bytes(out_dir, f"script_{index}.js", script_result.body)
        markers = sorted(
            set(
                marker
                for marker in [
                    "/api/webchat/fast-reply",
                    "/api/webchat/init",
                    "/api/webchat/conversations/",
                    "/api/webchat/voice/runtime-config",
                    "/webchat/live/ws",
                    "live/ws",
                    "voice-entry",
                ]
                if marker in body
            )
        )
        script_markers[script_url] = markers
    write_json(out_dir, "script_markers.json", script_markers)
    if not any("/api/webchat/fast-reply" in markers for markers in script_markers.values()):
        errors.append("fast_reply_marker_missing_from_scripts")

    allowed_cors = request(
        f"{base_url}/api/webchat/fast-reply",
        method="OPTIONS",
        headers={
            "Origin": args.origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
        timeout=args.timeout_seconds,
        allow_http_error=True,
    )
    write_bytes(out_dir, "cors_allowed_headers.txt", bytes(str(dict(allowed_cors.headers)), "utf-8"))
    allowed_origin = normalized_header(allowed_cors.headers, "access-control-allow-origin")
    if allowed_cors.status >= 400 or allowed_origin not in {args.origin, "*"}:
        errors.append(f"cors_allowed_failed status={allowed_cors.status} allow_origin={allowed_origin!r}")

    if args.blocked_origin:
        blocked_cors = request(
            f"{base_url}/api/webchat/fast-reply",
            method="OPTIONS",
            headers={
                "Origin": args.blocked_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=args.timeout_seconds,
            allow_http_error=True,
        )
        write_bytes(out_dir, "cors_blocked_headers.txt", bytes(str(dict(blocked_cors.headers)), "utf-8"))
        blocked_origin = normalized_header(blocked_cors.headers, "access-control-allow-origin")
        if blocked_cors.status < 400 and blocked_origin in {args.blocked_origin, "*"}:
            errors.append(f"cors_blocked_origin_allowed status={blocked_cors.status} allow_origin={blocked_origin!r}")

    fast_reply_summary: dict[str, object] | None = None
    if not args.skip_fast_reply:
        session_id = f"public_webchat_smoke_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant_key": args.tenant_key,
            "channel_key": "website",
            "session_id": session_id,
            "client_message_id": f"{session_id}_1",
            "body": args.message,
            "recent_context": [],
            "visitor": {"name": "Public Smoke"},
            "country_code": args.country_code,
            "market_code": args.market_code,
        }
        write_json(out_dir, "fast_reply_request.json", payload)
        fast_result = request(
            f"{base_url}/api/webchat/fast-reply",
            method="POST",
            headers={"Origin": args.origin, "Accept": "application/json"},
            payload=payload,
            timeout=args.fast_reply_timeout_seconds,
        )
        write_bytes(out_dir, "fast_reply_response.json", fast_result.body)
        fast_payload = fast_result.json()
        if not isinstance(fast_payload, dict):
            errors.append("fast_reply_not_json_object")
            fast_payload = {}
        reply = str(fast_payload.get("reply") or "")
        reply_source = str(fast_payload.get("reply_source") or "")
        fast_reply_summary = {
            "status": fast_result.status,
            "elapsed_ms": fast_result.elapsed_ms,
            "ok": fast_payload.get("ok"),
            "ai_generated": fast_payload.get("ai_generated"),
            "reply_source": reply_source,
            "intent": fast_payload.get("intent"),
            "handoff_required": fast_payload.get("handoff_required"),
            "reply_chars": len(reply),
            "reply_starts_json": reply.lstrip().startswith("{"),
            "reply_preview": reply[:240],
            "session_id": session_id,
        }
        if fast_result.status != 200:
            errors.append(f"fast_reply_status={fast_result.status}")
        if fast_payload.get("ok") is not True:
            errors.append(f"fast_reply_ok={fast_payload.get('ok')}")
        if not reply.strip():
            errors.append("fast_reply_empty_reply")
        if reply.lstrip().startswith("{"):
            errors.append("fast_reply_reply_looks_like_json")
        if fast_payload.get("handoff_required") is True and not args.allow_handoff:
            errors.append(f"fast_reply_handoff_required={fast_payload.get('handoff_reason')}")
        if args.require_ai_reply:
            if fast_payload.get("ai_generated") is not True:
                errors.append(f"fast_reply_ai_generated={fast_payload.get('ai_generated')}")
            if reply_source not in DEFAULT_ALLOWED_REPLY_SOURCES:
                errors.append(f"fast_reply_reply_source={reply_source}")
        if args.max_latency_ms > 0 and fast_result.elapsed_ms > args.max_latency_ms:
            errors.append(f"fast_reply_latency_ms={fast_result.elapsed_ms} max={args.max_latency_ms}")

    summary = {
        "base_url": base_url,
        "healthz": {
            "status": healthz.get("status"),
            "git_sha": healthz.get("git_sha"),
            "image_tag": healthz.get("image_tag"),
            "release_metadata_complete": healthz.get("release_metadata_complete"),
        },
        "readyz": {
            "status": readyz.get("status"),
            "database": readyz.get("database"),
            "migration_revision": readyz.get("migration_revision"),
            "release_metadata_complete": readyz.get("release_metadata_complete"),
            "storage": readyz.get("storage"),
        },
        "script_markers": script_markers,
        "fast_reply": fast_reply_summary,
        "errors": errors,
    }
    write_json(out_dir, "summary.json", summary)
    fail(errors)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Secret-free public WebChat production smoke.")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "https://www.leakle.com"))
    parser.add_argument("--origin", default=os.getenv("WEBCHAT_SMOKE_ORIGIN", "https://www.leakle.com"))
    parser.add_argument("--blocked-origin", default=os.getenv("WEBCHAT_SMOKE_BLOCKED_ORIGIN", "https://evil.example"))
    parser.add_argument("--expected-git-sha", default=os.getenv("EXPECTED_GIT_SHA", ""))
    parser.add_argument("--expected-image-tag", default=os.getenv("EXPECTED_IMAGE_TAG", ""))
    parser.add_argument("--evidence-dir", default=os.getenv("OUT_DIR", f"artifacts/public_webchat_smoke_{int(time.time())}"))
    parser.add_argument("--tenant-key", default=os.getenv("WEBCHAT_SMOKE_TENANT_KEY", "default"))
    parser.add_argument("--country-code", default=os.getenv("WEBCHAT_SMOKE_COUNTRY_CODE", "DE"))
    parser.add_argument("--market-code", default=os.getenv("WEBCHAT_SMOKE_MARKET_CODE", "DE"))
    parser.add_argument(
        "--message",
        default=os.getenv(
            "WEBCHAT_SMOKE_MESSAGE",
            "Please help me track my parcel. I will provide the tracking number.",
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("WEBCHAT_SMOKE_TIMEOUT_SECONDS", "12")))
    parser.add_argument(
        "--fast-reply-timeout-seconds",
        type=float,
        default=float(os.getenv("WEBCHAT_FAST_REPLY_SMOKE_TIMEOUT_SECONDS", "45")),
    )
    parser.add_argument("--max-latency-ms", type=int, default=env_int("WEBCHAT_FAST_REPLY_MAX_LATENCY_MS", 25000))
    parser.add_argument(
        "--require-release-metadata-complete",
        action=argparse.BooleanOptionalAction,
        default=env_bool("REQUIRE_RELEASE_METADATA_COMPLETE", True),
    )
    parser.add_argument(
        "--require-ai-reply",
        action=argparse.BooleanOptionalAction,
        default=env_bool("REQUIRE_AI_REPLY", True),
    )
    parser.add_argument(
        "--skip-fast-reply",
        action=argparse.BooleanOptionalAction,
        default=env_bool("SKIP_FAST_REPLY", False),
    )
    parser.add_argument(
        "--allow-handoff",
        action=argparse.BooleanOptionalAction,
        default=env_bool("WEBCHAT_SMOKE_ALLOW_HANDOFF", False),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run(args)
    print("PUBLIC_WEBCHAT_SMOKE_PASS=true")
    print("base_url=" + str(summary["base_url"]))
    print("git_sha=" + str(summary["healthz"].get("git_sha")))
    print("image_tag=" + str(summary["healthz"].get("image_tag")))
    fast_reply = summary.get("fast_reply") or {}
    if isinstance(fast_reply, dict):
        print("fast_reply_source=" + str(fast_reply.get("reply_source")))
        print("fast_reply_elapsed_ms=" + str(fast_reply.get("elapsed_ms")))
    print("evidence_dir=" + str(args.evidence_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
