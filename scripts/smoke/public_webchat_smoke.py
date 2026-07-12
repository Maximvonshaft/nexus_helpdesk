#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


FORBIDDEN_MARKERS = tuple(
    "".join(parts)
    for parts in (
        ("/api/webchat/", "fast", "-reply"),
        ("webchat", "_", "fast"),
        ("fast", "_", "reply"),
        ("co", "dex", "_app_server"),
        ("co", "dex", "_direct"),
        ("openai", "_responses"),
        ("Please provide your ", "tracking number"),
    )
)
SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "password",
    "secret",
    "authorization",
    "cookie",
    "api_key",
    "contact",
)
SENSITIVE_EXACT_KEYS = {
    "body",
    "body_text",
    "metadata_json",
    "email",
    "phone",
    "address",
    "to_address",
}
REDACTED = "[REDACTED]"


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
        "User-Agent": os.getenv("WEBCHAT_SMOKE_USER_AGENT", "nexus-webchat-smoke/1.0"),
        "Accept": "application/json",
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
            return HttpResult(
                resp.status,
                dict(resp.headers.items()),
                body,
                int((time.monotonic() - started) * 1000),
            )
    except HTTPError as exc:
        body = exc.read()
        if allow_http_error:
            return HttpResult(
                exc.code,
                dict(exc.headers.items()),
                body,
                int((time.monotonic() - started) * 1000),
            )
        raise
    except URLError:
        raise


def _sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower()
    return normalized in SENSITIVE_EXACT_KEYS or any(
        fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS
    )


def redact_sensitive(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _sensitive_key(key) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value


def write_json(out_dir: Path, name: str, payload: object) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_payload = redact_sensitive(payload)
    (out_dir / name).write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def write_bytes(out_dir: Path, name: str, payload: bytes) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_bytes(payload)


def parse_json_result(result: HttpResult) -> dict[str, Any]:
    try:
        payload = result.json()
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_endpoint_evidence(
    out_dir: Path,
    name: str,
    result: HttpResult,
    payload: dict[str, Any],
) -> None:
    write_json(
        out_dir,
        name,
        {
            "http_status": result.status,
            "elapsed_ms": result.elapsed_ms,
            "json_valid": bool(payload),
            "payload": payload if payload else None,
        },
    )


def summarize_endpoint(result: HttpResult, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.status,
        "elapsed_ms": result.elapsed_ms,
        "ok": 200 <= result.status < 300,
        "payload_status": payload.get("status") or payload.get("ok"),
    }


def assert_demo_has_no_retired_markers(
    base_url: str,
    out_dir: Path,
    errors: list[str],
) -> dict[str, Any]:
    demo = request(
        urljoin(base_url, "/webchat/demo/"),
        headers={"Accept": "text/html"},
        timeout=20,
    )
    write_bytes(out_dir, "webchat_demo.html", demo.body)
    html = demo.text()
    found = [marker for marker in FORBIDDEN_MARKERS if marker in html]
    if found:
        errors.append("webchat_demo_retired_markers=" + ",".join(found))
    return {
        "status": demo.status,
        "elapsed_ms": demo.elapsed_ms,
        "ok": 200 <= demo.status < 300,
        "retired_markers": found,
    }


def release_metadata_complete(
    health_payload: dict[str, Any],
    *,
    expected_git_sha: str,
    expected_image_tag: str,
    errors: list[str],
) -> bool:
    if not expected_git_sha and not expected_image_tag:
        return True
    ok = True
    if expected_git_sha:
        actual_git_sha = str(
            health_payload.get("git_sha") or health_payload.get("commit_sha") or ""
        )
        if actual_git_sha != expected_git_sha:
            errors.append(
                f"release_git_sha_mismatch expected={expected_git_sha} "
                f"actual={actual_git_sha or '[missing]'}"
            )
            ok = False
    if expected_image_tag:
        actual_image_tag = str(
            health_payload.get("image_tag")
            or health_payload.get("release_image_tag")
            or ""
        )
        if actual_image_tag != expected_image_tag:
            errors.append(
                f"release_image_tag_mismatch expected={expected_image_tag} "
                f"actual={actual_image_tag or '[missing]'}"
            )
            ok = False
    return ok


def find_ai_reply(
    messages: list[dict[str, Any]],
    visitor_message_id: int,
) -> dict[str, Any] | None:
    for item in messages:
        if int(item.get("id") or 0) <= visitor_message_id:
            continue
        direction = str(item.get("direction") or item.get("author") or "")
        body = str(item.get("body") or item.get("body_text") or "").strip()
        if direction in {"ai", "agent", "system"} and body:
            return item
    return None


def is_private_ai_runtime_source(reply_source: str) -> bool:
    return reply_source == "private_ai_runtime" or reply_source.startswith(
        "private_ai_runtime:"
    )


def reply_source_error(
    reply_source: str,
    *,
    require_ai_reply: bool,
) -> str | None:
    if not require_ai_reply:
        return None
    if not reply_source:
        return "webchat_reply_source_missing"
    if not is_private_ai_runtime_source(reply_source):
        return f"webchat_reply_source={reply_source}"
    return None


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean")


def normalize_http_endpoint(value: str, *, name: str) -> str:
    raw = value.strip()
    if not raw:
        raise SystemExit(f"{name} is required")
    if len(raw) > 2048 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise SystemExit(f"{name} contains control characters or is too long")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SystemExit(f"{name} must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit(f"{name} must contain a safe hostname without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SystemExit(f"{name} must be a root URL or exact Origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"{name} contains an invalid port") from exc
    host = parsed.hostname.lower()
    if ":" in host:
        raise SystemExit(f"{name} IPv6 literals are not supported by this smoke")
    authority = host if port is None else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Public WebChat AI Runtime smoke")
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://127.0.0.1:18082"),
    )
    parser.add_argument(
        "--origin",
        default=os.getenv(
            "WEBCHAT_SMOKE_ORIGIN",
            os.getenv("ORIGIN", "https://www.leakle.com"),
        ),
    )
    parser.add_argument(
        "--message",
        default=os.getenv("WEBCHAT_SMOKE_MESSAGE", "你好"),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("WEBCHAT_SMOKE_TIMEOUT_SECONDS", "90")),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=float(os.getenv("WEBCHAT_SMOKE_POLL_SECONDS", "2")),
    )
    parser.add_argument(
        "--max-reply-ms",
        type=int,
        default=int(os.getenv("WEBCHAT_AI_REPLY_MAX_LATENCY_MS", "0") or "0"),
    )
    parser.add_argument(
        "--out-dir",
        default=os.getenv("OUT_DIR", "artifacts/public_webchat_smoke"),
    )
    parser.add_argument(
        "--expected-git-sha",
        default=os.getenv("EXPECTED_GIT_SHA", ""),
    )
    parser.add_argument(
        "--expected-image-tag",
        default=os.getenv("EXPECTED_IMAGE_TAG", ""),
    )
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument(
        "--require-ai-reply",
        action=argparse.BooleanOptionalAction,
        default=env_bool("REQUIRE_AI_REPLY", False),
    )
    args = parser.parse_args()

    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.poll_interval_seconds <= 0:
        parser.error("--poll-interval-seconds must be positive")
    if args.max_reply_ms < 0:
        parser.error("--max-reply-ms must be zero or positive")
    if args.allow_pending and args.require_ai_reply:
        parser.error("--allow-pending conflicts with --require-ai-reply")

    base_url = normalize_http_endpoint(args.base_url, name="base_url") + "/"
    origin = normalize_http_endpoint(args.origin, name="origin")
    out_dir = Path(args.out_dir)
    errors: list[str] = []
    summary: dict[str, Any] = {
        "healthz": None,
        "readyz": None,
        "release_metadata_complete": False,
        "reply_starts_json": False,
        "require_ai_reply": args.require_ai_reply,
        "reply_source": None,
        "reply_elapsed_ms": None,
        "errors": errors,
    }

    try:
        health = request(
            urljoin(base_url, "/healthz"),
            timeout=20,
            allow_http_error=True,
        )
        health_payload = parse_json_result(health)
        write_endpoint_evidence(out_dir, "healthz.json", health, health_payload)
        summary["healthz"] = summarize_endpoint(health, health_payload)
        if not summary["healthz"]["ok"]:
            errors.append(f"healthz_status={health.status}")

        ready = request(
            urljoin(base_url, "/readyz"),
            timeout=20,
            allow_http_error=True,
        )
        ready_payload = parse_json_result(ready)
        write_endpoint_evidence(out_dir, "readyz.json", ready, ready_payload)
        summary["readyz"] = summarize_endpoint(ready, ready_payload)
        if not summary["readyz"]["ok"]:
            errors.append(f"readyz_status={ready.status}")

        summary["release_metadata_complete"] = release_metadata_complete(
            health_payload,
            expected_git_sha=args.expected_git_sha.strip(),
            expected_image_tag=args.expected_image_tag.strip(),
            errors=errors,
        )

        summary["webchat_demo"] = assert_demo_has_no_retired_markers(
            base_url,
            out_dir,
            errors,
        )

        init_payload = {
            "tenant_key": "default",
            "channel_key": "website",
            "visitor_name": "Public Smoke",
            "origin": origin,
            "page_url": urljoin(base_url, "/webchat/demo/"),
        }
        init = request(
            urljoin(base_url, "/api/webchat/init"),
            method="POST",
            headers={"Origin": origin},
            payload=init_payload,
            timeout=20,
        )
        init_json = init.json()
        write_json(out_dir, "init_response.json", init_json)
        if (
            not isinstance(init_json, dict)
            or not init_json.get("conversation_id")
            or not init_json.get("visitor_token")
        ):
            errors.append("webchat_init_invalid")
            raise SystemExit(1)

        conversation_id = str(init_json["conversation_id"])
        visitor_token = str(init_json["visitor_token"])
        client_message_id = "public-smoke-" + uuid.uuid4().hex[:12]
        send_payload = {
            "body": args.message,
            "client_message_id": client_message_id,
        }
        send = request(
            urljoin(
                base_url,
                f"/api/webchat/conversations/{conversation_id}/messages",
            ),
            method="POST",
            headers={
                "Origin": origin,
                "X-Webchat-Visitor-Token": visitor_token,
            },
            payload=send_payload,
            timeout=20,
        )
        send_json = send.json()
        write_json(out_dir, "send_response.json", send_json)
        summary["reply_starts_json"] = isinstance(send_json, dict) and (
            send_json.get("ok") is True or bool(send_json.get("ai_pending"))
        )
        if not isinstance(send_json, dict) or send_json.get("ok") is not True:
            errors.append("webchat_send_invalid")
            raise SystemExit(1)
        visitor_message_id = int(((send_json.get("message") or {}).get("id")) or 0)
        if visitor_message_id <= 0:
            errors.append("webchat_send_missing_message_id")
            raise SystemExit(1)

        reply_started = time.monotonic()
        deadline = time.monotonic() + max(1.0, args.timeout_seconds)
        last_poll: dict[str, Any] | None = None
        reply: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = request(
                urljoin(
                    base_url,
                    f"/api/webchat/conversations/{conversation_id}/messages?limit=50",
                ),
                headers={
                    "Origin": origin,
                    "X-Webchat-Visitor-Token": visitor_token,
                },
                timeout=20,
            )
            payload = poll.json()
            write_json(out_dir, "poll_last_response.json", payload)
            if isinstance(payload, dict):
                last_poll = payload
                messages = payload.get("messages")
                if isinstance(messages, list):
                    reply = find_ai_reply(
                        [item for item in messages if isinstance(item, dict)],
                        visitor_message_id,
                    )
                    if reply:
                        break
            time.sleep(max(0.2, args.poll_interval_seconds))

        if not reply:
            pending_ok = args.allow_pending and not args.require_ai_reply
            message_count = 0
            if isinstance(last_poll, dict) and isinstance(last_poll.get("messages"), list):
                message_count = len(last_poll["messages"])
            summary.update(
                {
                    "ok": pending_ok,
                    "conversation_id": conversation_id,
                    "pending": True,
                    "last_poll_observed": last_poll is not None,
                    "last_poll_message_count": message_count,
                }
            )
            if pending_ok:
                write_json(out_dir, "summary.json", summary)
                print("public_webchat_pending=true")
                print("PUBLIC_WEBCHAT_SMOKE_PASS=true")
                return 0
            errors.append("webchat_ai_reply_missing")
            raise SystemExit(1)

        reply_elapsed_ms = int((time.monotonic() - reply_started) * 1000)
        summary["reply_elapsed_ms"] = reply_elapsed_ms
        if args.max_reply_ms > 0 and reply_elapsed_ms > args.max_reply_ms:
            errors.append(f"webchat_ai_reply_latency_ms={reply_elapsed_ms}")

        metadata = (
            reply.get("metadata_json")
            if isinstance(reply.get("metadata_json"), dict)
            else {}
        )
        reply_source = str(metadata.get("reply_source") or "")
        summary["reply_source"] = reply_source or None
        source_error = reply_source_error(
            reply_source,
            require_ai_reply=args.require_ai_reply,
        )
        if source_error:
            errors.append(source_error)
        body = str(reply.get("body") or reply.get("body_text") or "")
        if any(marker in body for marker in FORBIDDEN_MARKERS):
            errors.append("webchat_reply_contains_retired_marker")

        summary.update(
            {
                "ok": not errors,
                "conversation_id": conversation_id,
                "visitor_message_id": visitor_message_id,
                "reply_message_id": reply.get("id"),
                "reply_chars": len(body),
            }
        )
        write_json(out_dir, "summary.json", summary)
        if errors:
            raise SystemExit(1)
        print("PUBLIC_WEBCHAT_SMOKE_PASS=true")
        print("reply_source=" + (reply_source or "unknown"))
        print(f"reply_elapsed_ms={reply_elapsed_ms}")
        print("evidence_dir=" + str(out_dir))
        return 0
    except Exception as exc:
        if not errors:
            errors.append(f"{type(exc).__name__}: {exc}")
        write_json(out_dir, "summary.json", summary)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
