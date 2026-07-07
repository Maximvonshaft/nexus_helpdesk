#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

RELEASE_FIELDS = ("git_sha", "image_tag", "build_time", "frontend_build_sha")
RETIRED_MARKERS = ("/api/webchat/fast-reply", "webchat_fast", "fast_reply")


def _read(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, payload: object | None = None, allow_http_error: bool = False, timeout: float = 20.0) -> dict[str, Any]:
    body = None
    request_headers = {"User-Agent": os.getenv("WEBCHAT_SMOKE_USER_AGENT", "nexus-webchat-smoke/1.0"), "Accept": "application/json"}
    request_headers.update(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    started = time.monotonic()
    req = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            status = resp.status
            response_headers = dict(resp.headers.items())
    except HTTPError as exc:
        data = exc.read()
        status = exc.code
        response_headers = dict(exc.headers.items())
        if not allow_http_error:
            raise
    return {"status": status, "headers": response_headers, "body": data, "text": data.decode("utf-8", errors="replace"), "elapsed_ms": int((time.monotonic() - started) * 1000)}


def _json(value: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(value.get("text") or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write(out_dir: Path, name: str, value: object) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        (out_dir / name).write_bytes(value)
    else:
        (out_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _endpoint(base_url: str, path: str, out_dir: Path) -> dict[str, Any]:
    result = _read(urljoin(base_url, path), allow_http_error=True)
    payload = _json(result)
    _write(out_dir, path.strip("/") + ".json", result["body"])
    return {"status": result["status"], "ok": 200 <= int(result["status"]) < 300, "elapsed_ms": result["elapsed_ms"], "payload": payload}


def _metadata_complete(payloads: list[dict[str, Any]], expected_git_sha: str | None, expected_image_tag: str | None) -> bool:
    for payload in payloads:
        if not payload:
            return False
        if payload.get("release_metadata_complete") is not True:
            return False
        if any(not str(payload.get(field) or "").strip() or payload.get(field) == "unknown" for field in RELEASE_FIELDS):
            return False
        if expected_git_sha and payload.get("git_sha") != expected_git_sha:
            return False
        if expected_image_tag and payload.get("image_tag") != expected_image_tag:
            return False
    return True


def _find_reply(messages: list[dict[str, Any]], visitor_message_id: int) -> dict[str, Any] | None:
    for item in messages:
        if int(item.get("id") or 0) <= visitor_message_id:
            continue
        body = str(item.get("body") or item.get("body_text") or "").strip()
        if str(item.get("direction") or "") in {"ai", "agent", "system"} and body:
            return item
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Public WebChat runtime smoke")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:18082"))
    parser.add_argument("--origin", default=os.getenv("ORIGIN", "https://www.leakle.com"))
    parser.add_argument("--message", default=os.getenv("WEBCHAT_SMOKE_MESSAGE", "你好"))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("WEBCHAT_SMOKE_TIMEOUT_SECONDS", "90")))
    parser.add_argument("--poll-interval-seconds", type=float, default=float(os.getenv("WEBCHAT_SMOKE_POLL_SECONDS", "2")))
    parser.add_argument("--max-reply-ms", type=int, default=int(os.getenv("WEBCHAT_AI_REPLY_MAX_LATENCY_MS", "0") or "0"))
    parser.add_argument("--expected-git-sha", default=os.getenv("EXPECTED_GIT_SHA"))
    parser.add_argument("--expected-image-tag", default=os.getenv("EXPECTED_IMAGE_TAG"))
    parser.add_argument("--require-ai-reply", action="store_true", default=os.getenv("REQUIRE_AI_REPLY", "true").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--out-dir", default=os.getenv("OUT_DIR", "artifacts/public_webchat_smoke"))
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/") + "/"
    out_dir = Path(args.out_dir)
    errors: list[str] = []

    healthz = _endpoint(base_url, "/healthz", out_dir)
    readyz = _endpoint(base_url, "/readyz", out_dir)
    release_metadata_complete = _metadata_complete([healthz["payload"], readyz["payload"]], args.expected_git_sha, args.expected_image_tag)
    if not healthz["ok"]:
        errors.append("healthz_not_ok")
    if not readyz["ok"]:
        errors.append("readyz_not_ok")
    if (args.expected_git_sha or args.expected_image_tag) and not release_metadata_complete:
        errors.append("release_metadata_mismatch")

    demo = _read(urljoin(base_url, "/webchat/demo/"), headers={"Accept": "text/html"})
    _write(out_dir, "webchat_demo.html", demo["body"])
    if any(marker in demo["text"] for marker in RETIRED_MARKERS):
        raise SystemExit("webchat_demo_retired_marker")

    init_payload = {"tenant_key": "default", "channel_key": "website", "visitor_name": "Public Smoke", "origin": args.origin, "page_url": urljoin(base_url, "/webchat/demo/")}
    init = _read(urljoin(base_url, "/api/webchat/init"), method="POST", headers={"Origin": args.origin}, payload=init_payload)
    _write(out_dir, "init_response.json", init["body"])
    init_json = _json(init)
    if not init_json.get("conversation_id") or not init_json.get("visitor_token"):
        raise SystemExit("webchat_init_invalid")

    conversation_id = str(init_json["conversation_id"])
    visitor_token = str(init_json["visitor_token"])
    send_payload = {"body": args.message, "client_message_id": "public-smoke-" + uuid.uuid4().hex[:12]}
    send = _read(urljoin(base_url, f"/api/webchat/conversations/{conversation_id}/messages"), method="POST", headers={"Origin": args.origin, "X-Webchat-Visitor-Token": visitor_token}, payload=send_payload)
    _write(out_dir, "send_response.json", send["body"])
    send_json = _json(send)
    visitor_message_id = int(((send_json.get("message") or {}).get("id")) or 0)
    if send_json.get("ok") is not True or visitor_message_id <= 0:
        raise SystemExit("webchat_send_invalid")

    deadline = time.monotonic() + max(1.0, args.timeout_seconds)
    started = time.monotonic()
    last_poll: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None
    reply_starts_json = False
    while time.monotonic() < deadline:
        poll = _read(urljoin(base_url, f"/api/webchat/conversations/{conversation_id}/messages?limit=50"), headers={"Origin": args.origin, "X-Webchat-Visitor-Token": visitor_token})
        _write(out_dir, "poll_last_response.json", poll["body"])
        reply_starts_json = str(poll.get("text") or "").lstrip().startswith("{")
        payload = _json(poll)
        last_poll = payload
        messages = payload.get("messages")
        if isinstance(messages, list):
            reply = _find_reply([item for item in messages if isinstance(item, dict)], visitor_message_id)
            if reply:
                break
        time.sleep(max(0.2, args.poll_interval_seconds))

    reply_elapsed_ms = int((time.monotonic() - started) * 1000)
    metadata = reply.get("metadata_json") if isinstance(reply, dict) and isinstance(reply.get("metadata_json"), dict) else {}
    reply_source = str(metadata.get("reply_source") or "")
    if not reply and args.require_ai_reply and not args.allow_pending:
        errors.append("webchat_ai_reply_missing")
    if args.max_reply_ms > 0 and reply and reply_elapsed_ms > args.max_reply_ms:
        errors.append("reply_latency_exceeded")

    summary = {"ok": not errors, "healthz": healthz, "readyz": readyz, "release_metadata_complete": release_metadata_complete, "reply_starts_json": reply_starts_json, "reply_source": reply_source or None, "reply_elapsed_ms": reply_elapsed_ms if reply else None, "errors": errors, "conversation_id": conversation_id, "last_poll": last_poll if not reply else None}
    _write(out_dir, "summary.json", summary)
    if errors:
        raise SystemExit("public_webchat_smoke_failed=" + ",".join(errors))
    print("PUBLIC_WEBCHAT_SMOKE_PASS=true")
    print("reply_source=" + (reply_source or "unknown"))
    print(f"reply_elapsed_ms={reply_elapsed_ms}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
