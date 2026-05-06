#!/usr/bin/env python3
"""Minimal WebChat smoke/load probe for staging or local environments.

Default target is local only. Passing a production URL will create real WebChat
conversations/messages there, so use a staging/local base URL unless a controlled
production smoke window has been explicitly approved.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProbeStats:
    init_ms: list[float] = field(default_factory=list)
    send_ack_ms: list[float] = field(default_factory=list)
    poll_ms: list[float] = field(default_factory=list)
    total_ms: list[float] = field(default_factory=list)
    success_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "ProbeStats") -> None:
        self.init_ms.extend(other.init_ms)
        self.send_ack_ms.extend(other.send_ack_ms)
        self.poll_ms.extend(other.poll_ms)
        self.total_ms.extend(other.total_ms)
        self.success_count += other.success_count
        self.error_count += other.error_count
        self.errors.extend(other.errors)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "max": round(max(values), 2),
    }


def _request_json(base_url: str, path: str, payload: dict[str, Any] | None = None, *, method: str = "GET", headers: dict[str, str] | None = None, timeout: float = 20.0) -> tuple[dict[str, Any], float]:
    url = base_url.rstrip("/") + path
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return json.loads(raw or "{}"), elapsed_ms


def _run_conversation(args: argparse.Namespace, conversation_index: int) -> ProbeStats:
    stats = ProbeStats()
    total_started = time.perf_counter()
    origin_headers = {"Origin": args.origin, "Referer": args.origin + "/smoke"} if args.origin else {}
    try:
        init_payload = {
            "tenant_key": args.tenant_key,
            "channel_key": args.channel_key,
            "visitor_name": f"Smoke Visitor {conversation_index}",
            "visitor_ref": f"smoke-{int(time.time())}-{conversation_index}",
            "origin": args.origin,
            "page_url": args.origin + "/smoke" if args.origin else None,
        }
        init_data, init_ms = _request_json(args.base_url, "/api/webchat/init", init_payload, method="POST", headers=origin_headers, timeout=args.timeout)
        stats.init_ms.append(init_ms)
        conversation_id = init_data["conversation_id"]
        visitor_token = init_data["visitor_token"]
        token_headers = {**origin_headers, "X-Webchat-Visitor-Token": visitor_token}

        for message_index in range(args.messages_per_conversation):
            send_payload = {
                "body": f"Smoke message {message_index + 1} from conversation {conversation_index}. Please acknowledge.",
                "client_message_id": f"smoke-{conversation_index}-{message_index}-{int(time.time() * 1000)}",
            }
            _, send_ms = _request_json(
                args.base_url,
                f"/api/webchat/conversations/{conversation_id}/messages",
                send_payload,
                method="POST",
                headers=token_headers,
                timeout=args.timeout,
            )
            stats.send_ack_ms.append(send_ms)
            if args.delay_ms > 0:
                time.sleep(args.delay_ms / 1000.0)

        _, poll_ms = _request_json(
            args.base_url,
            f"/api/webchat/conversations/{conversation_id}/messages?after_id=0&limit=100",
            None,
            method="GET",
            headers=token_headers,
            timeout=args.timeout,
        )
        stats.poll_ms.append(poll_ms)
        stats.total_ms.append((time.perf_counter() - total_started) * 1000.0)
        stats.success_count += 1
    except Exception as exc:
        stats.error_count += 1
        stats.errors.append(f"conversation={conversation_index}: {type(exc).__name__}: {exc}")
        stats.total_ms.append((time.perf_counter() - total_started) * 1000.0)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="WebChat local/staging smoke/load probe")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Target base URL. Defaults to local only.")
    parser.add_argument("--origin", default="http://localhost", help="Origin/Referer header used for WebChat origin allowlist checks.")
    parser.add_argument("--tenant-key", default="smoke")
    parser.add_argument("--channel-key", default="smoke")
    parser.add_argument("--conversations", type=int, default=1)
    parser.add_argument("--messages-per-conversation", type=int, default=1)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true", help="Print planned run without making HTTP requests.")
    args = parser.parse_args()

    if args.conversations < 1 or args.messages_per_conversation < 1:
        raise SystemExit("conversations and messages-per-conversation must be >= 1")

    plan = {
        "base_url": args.base_url,
        "origin": args.origin,
        "conversations": args.conversations,
        "messages_per_conversation": args.messages_per_conversation,
        "delay_ms": args.delay_ms,
        "load_gate_notes": {
            "10_conversations": "smoke",
            "20_conversations": "pilot gate",
            "50_conversations": "staging load gate",
            "100_conversations": "not committed by current architecture without further evidence",
        },
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, "plan": plan}, ensure_ascii=False, indent=2))
        return 0

    combined = ProbeStats()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(args.conversations, 20)) as executor:
        futures = [executor.submit(_run_conversation, args, index) for index in range(1, args.conversations + 1)]
        for future in as_completed(futures):
            combined.merge(future.result())

    result = {
        "plan": plan,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "success_count": combined.success_count,
        "error_count": combined.error_count,
        "init_ms": _summary(combined.init_ms),
        "send_ack_ms": _summary(combined.send_ack_ms),
        "poll_ms": _summary(combined.poll_ms),
        "total_ms": _summary(combined.total_ms),
        "errors": combined.errors[:20],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if combined.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
