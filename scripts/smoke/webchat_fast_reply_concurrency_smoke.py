#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Result:
    ok: bool
    status: int | None
    elapsed_ms: int
    error: str | None = None
    ai_generated: bool | None = None
    has_reply: bool | None = None


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> Result:
    started = time.perf_counter()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            parsed = json.loads(body or "{}")
            api_ok = bool(parsed.get("ok") is True and parsed.get("ai_generated") is True and parsed.get("reply"))
            return Result(
                ok=api_ok,
                status=resp.status,
                elapsed_ms=elapsed_ms,
                ai_generated=parsed.get("ai_generated"),
                has_reply=bool(parsed.get("reply")),
            )
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return Result(ok=False, status=exc.code, elapsed_ms=elapsed_ms, error=f"http_{exc.code}")
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return Result(ok=False, status=None, elapsed_ms=elapsed_ms, error=type(exc).__name__)


def _payload(index: int) -> dict[str, Any]:
    session_id = f"smoke_session_{index % 25}_{uuid.uuid4().hex[:8]}"
    return {
        "tenant_key": "default",
        "channel_key": "smoke",
        "session_id": session_id,
        "client_message_id": f"smoke_msg_{index}_{uuid.uuid4().hex[:8]}",
        "body": "Hi Speedy, can you help me track my parcel?",
        "recent_context": [],
    }


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * (len(ordered) - 1)))))
    return float(ordered[index])


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test NexusDesk WebChat fast reply concurrency")
    parser.add_argument("--base-url", required=True, help="NexusDesk base URL, e.g. http://127.0.0.1:18081")
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--p95-ms", type=int, default=5000)
    parser.add_argument("--max-ms", type=int, default=8000)
    parser.add_argument("--success-rate", type=float, default=0.95)
    args = parser.parse_args()

    if args.concurrency <= 0 or args.requests <= 0:
        print("concurrency and requests must be positive", file=sys.stderr)
        return 2

    url = args.base_url.rstrip("/") + "/api/webchat/fast-reply"
    started = time.perf_counter()
    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(_post_json, url, _payload(i), args.timeout) for i in range(args.requests)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    wall_ms = int((time.perf_counter() - started) * 1000)

    ok_count = sum(1 for item in results if item.ok)
    success_rate = ok_count / len(results) if results else 0.0
    elapsed = [item.elapsed_ms for item in results]
    p50 = statistics.median(elapsed) if elapsed else 0
    p95 = _percentile(elapsed, 95)
    max_ms = max(elapsed) if elapsed else 0
    errors: dict[str, int] = {}
    for item in results:
        if not item.ok:
            key = item.error or f"status_{item.status}" or "unknown"
            errors[key] = errors.get(key, 0) + 1

    report = {
        "target": url,
        "requests": len(results),
        "concurrency": args.concurrency,
        "success_count": ok_count,
        "success_rate": round(success_rate, 4),
        "p50_ms": p50,
        "p95_ms": p95,
        "max_ms": max_ms,
        "wall_ms": wall_ms,
        "errors": errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))

    failed = []
    if success_rate < args.success_rate:
        failed.append(f"success_rate {success_rate:.4f} < {args.success_rate}")
    if p95 > args.p95_ms:
        failed.append(f"p95_ms {p95} > {args.p95_ms}")
    if max_ms > args.max_ms:
        failed.append(f"max_ms {max_ms} > {args.max_ms}")
    if failed:
        print("FAILED: " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
