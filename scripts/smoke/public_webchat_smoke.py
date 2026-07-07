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
from urllib.parse import urljoin
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
            return HttpResult(resp.status, dict(resp.headers.items()), body, int((time.monotonic() - started) * 1000))
    except HTTPError as exc:
        body = exc.read()
        if allow_http_error:
            return HttpResult(exc.code, dict(exc.headers.items()), body, int((time.monotonic() - started) * 1000))
        raise
    except URLError:
        raise


def write_json(out_dir: Path, name: str, payload: object) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_bytes(out_dir: Path, name: str, payload: bytes) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_bytes(payload)


def assert_demo_has_no_retired_markers(base_url: str, out_dir: Path) -> None:
    demo = request(urljoin(base_url, "/webchat/demo/"), headers={"Accept": "text/html"}, timeout=20)
    write_bytes(out_dir, "webchat_demo.html", demo.body)
    html = demo.text()
    errors = [marker for marker in FORBIDDEN_MARKERS if marker in html]
    if errors:
        raise SystemExit("webchat_demo_retired_markers=" + ",".join(errors))


def find_ai_reply(messages: list[dict[str, Any]], visitor_message_id: int) -> dict[str, Any] | None:
    for item in messages:
        if int(item.get("id") or 0) <= visitor_message_id:
            continue
        direction = str(item.get("direction") or "")
        body = str(item.get("body") or item.get("body_text") or "").strip()
        if direction in {"ai", "agent", "system"} and body:
            return item
    return None


def is_private_ai_runtime_source(reply_source: str) -> bool:
    return reply_source == "private_ai_runtime" or reply_source.startswith("private_ai_runtime:")


def main() -> int:
    parser = argparse.ArgumentParser(description="Public WebChat AI Runtime smoke")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:18082"))
    parser.add_argument("--origin", default=os.getenv("ORIGIN", "https://www.leakle.com"))
    parser.add_argument("--message", default=os.getenv("WEBCHAT_SMOKE_MESSAGE", "你好"))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("WEBCHAT_SMOKE_TIMEOUT_SECONDS", "90")))
    parser.add_argument("--poll-interval-seconds", type=float, default=float(os.getenv("WEBCHAT_SMOKE_POLL_SECONDS", "2")))
    parser.add_argument("--max-reply-ms", type=int, default=int(os.getenv("WEBCHAT_AI_REPLY_MAX_LATENCY_MS", "0") or "0"))
    parser.add_argument("--out-dir", default=os.getenv("OUT_DIR", "artifacts/public_webchat_smoke"))
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/") + "/"
    out_dir = Path(args.out_dir)
    assert_demo_has_no_retired_markers(base_url, out_dir)

    init_payload = {
        "tenant_key": "default",
        "channel_key": "website",
        "visitor_name": "Public Smoke",
        "origin": args.origin,
        "page_url": urljoin(base_url, "/webchat/demo/"),
    }
    init = request(urljoin(base_url, "/api/webchat/init"), method="POST", headers={"Origin": args.origin}, payload=init_payload, timeout=20)
    write_bytes(out_dir, "init_response.json", init.body)
    init_json = init.json()
    if not isinstance(init_json, dict) or not init_json.get("conversation_id") or not init_json.get("visitor_token"):
        raise SystemExit("webchat_init_invalid")

    conversation_id = str(init_json["conversation_id"])
    visitor_token = str(init_json["visitor_token"])
    client_message_id = "public-smoke-" + uuid.uuid4().hex[:12]
    send_payload = {"body": args.message, "client_message_id": client_message_id}
    send = request(
        urljoin(base_url, f"/api/webchat/conversations/{conversation_id}/messages"),
        method="POST",
        headers={"Origin": args.origin, "X-Webchat-Visitor-Token": visitor_token},
        payload=send_payload,
        timeout=20,
    )
    write_bytes(out_dir, "send_response.json", send.body)
    send_json = send.json()
    if not isinstance(send_json, dict) or send_json.get("ok") is not True:
        raise SystemExit("webchat_send_invalid")
    visitor_message_id = int(((send_json.get("message") or {}).get("id")) or 0)
    if visitor_message_id <= 0:
        raise SystemExit("webchat_send_missing_message_id")

    reply_started = time.monotonic()
    deadline = time.monotonic() + max(1.0, args.timeout_seconds)
    last_poll: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll = request(
            urljoin(base_url, f"/api/webchat/conversations/{conversation_id}/messages?limit=50"),
            headers={"Origin": args.origin, "X-Webchat-Visitor-Token": visitor_token},
            timeout=20,
        )
        write_bytes(out_dir, "poll_last_response.json", poll.body)
        payload = poll.json()
        if isinstance(payload, dict):
            last_poll = payload
            messages = payload.get("messages")
            if isinstance(messages, list):
                reply = find_ai_reply([item for item in messages if isinstance(item, dict)], visitor_message_id)
                if reply:
                    break
        time.sleep(max(0.2, args.poll_interval_seconds))

    if not reply:
        write_json(out_dir, "summary.json", {"ok": args.allow_pending, "conversation_id": conversation_id, "pending": True, "last_poll": last_poll})
        if args.allow_pending:
            print("public_webchat_pending=true")
            return 0
        raise SystemExit("webchat_ai_reply_missing")

    reply_elapsed_ms = int((time.monotonic() - reply_started) * 1000)
    if args.max_reply_ms > 0 and reply_elapsed_ms > args.max_reply_ms:
        raise SystemExit(f"webchat_ai_reply_latency_ms={reply_elapsed_ms}")

    metadata = reply.get("metadata_json") if isinstance(reply.get("metadata_json"), dict) else {}
    reply_source = str(metadata.get("reply_source") or "")
    if reply_source and not is_private_ai_runtime_source(reply_source):
        raise SystemExit(f"webchat_reply_source={reply_source}")
    body = str(reply.get("body") or reply.get("body_text") or "")
    if any(marker in body for marker in FORBIDDEN_MARKERS):
        raise SystemExit("webchat_reply_contains_retired_marker")

    summary = {
        "ok": True,
        "conversation_id": conversation_id,
        "visitor_message_id": visitor_message_id,
        "reply_message_id": reply.get("id"),
        "reply_source": reply_source or None,
        "reply_chars": len(body),
        "reply_elapsed_ms": reply_elapsed_ms,
    }
    write_json(out_dir, "summary.json", summary)
    print("public_webchat_ok=true")
    print("reply_source=" + (reply_source or "unknown"))
    print(f"reply_elapsed_ms={reply_elapsed_ms}")
    print("evidence_dir=" + str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
