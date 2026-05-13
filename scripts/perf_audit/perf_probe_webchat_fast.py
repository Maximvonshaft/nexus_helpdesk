from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx

FORBIDDEN = [
    'OpenClaw',
    'gateway',
    'prompt',
    'system prompt',
    'developer message',
    'token',
    'localhost',
    '127.0.0.1',
    'port',
    'Authorization',
    'Bearer',
]


def _normalize_visible(text: str) -> str:
    return ' '.join(str(text or '').split()).lower()


def _contains_forbidden(text: str) -> bool:
    normalized = _normalize_visible(text)
    return any(_normalize_visible(term) in normalized for term in FORBIDDEN)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=20, method='inclusive')[18])


def _payload(seq: int) -> dict[str, Any]:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': f'perf-session-{seq % 10}',
        'client_message_id': f'perf-client-{seq}-{int(time.time() * 1000)}',
        'body': 'Hi, I need help tracking my parcel.',
        'recent_context': [],
    }


def _parse_sse_events(buffer: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in buffer.split('\n\n'):
        if not block.strip():
            continue
        event_name = 'message'
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith('event:'):
                event_name = line.split(':', 1)[1].strip()
            elif line.startswith('data:'):
                data_lines.append(line.split(':', 1)[1].lstrip())
        if not data_lines:
            continue
        raw_data = '\n'.join(data_lines)
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            payload = {'_malformed': raw_data}
        if isinstance(payload, dict):
            events.append((event_name, payload))
    return events


@dataclass
class ProbeResult:
    ok: bool
    first_chunk_ms: float
    total_ms: float
    raw_leak_count: int
    replayed: bool
    fallback: bool
    stream_success: bool
    stream_error: bool
    error_code: str | None = None


async def _probe_one(
    client: httpx.AsyncClient, 
    base_url: str, 
    seq: int, 
    *, 
    require_stream: bool, 
    expect_stream_disabled: bool = False, 
    expect_stream_not_in_rollout: bool = False,
    force_stream_canary_header: bool = False
) -> ProbeResult:
    payload = _payload(seq)
    started = time.perf_counter()
    first_chunk_ms: float | None = None
    visible: list[str] = []
    error_code: str | None = None
    replayed = False
    try:
        headers = {'Accept': 'text/event-stream', 'Content-Type': 'application/json'}
        if force_stream_canary_header:
            headers['X-Nexus-Stream-Canary'] = '1'

        async with client.stream(
            'POST',
            f'{base_url}/api/webchat/fast-reply/stream',
            json=payload,
            headers=headers,
        ) as response:
            content_type = response.headers.get('content-type', '')
            
            if expect_stream_disabled and response.status_code == 503:
                data = await response.json()
                if data.get('error_code') == 'stream_disabled':
                    return ProbeResult(True, 0.0, (time.perf_counter() - started) * 1000, 0, False, False, False, False, 'stream_disabled')
                    
            if expect_stream_not_in_rollout and response.status_code == 503:
                data = await response.json()
                if data.get('error_code') == 'stream_not_in_rollout':
                    return ProbeResult(True, 0.0, (time.perf_counter() - started) * 1000, 0, False, False, False, False, 'stream_not_in_rollout')
                    
            if response.status_code != 200 or 'text/event-stream' not in content_type:
                if require_stream:
                    data = {}
                    try:
                        data = await response.json()
                    except Exception:
                        pass
                    return ProbeResult(False, 0.0, (time.perf_counter() - started) * 1000, 0, False, False, False, True, str(data.get('error_code') or f'stream_http_{response.status_code}'))
                fallback_started = time.perf_counter()
                fallback = await client.post(f'{base_url}/api/webchat/fast-reply', json=payload)
                elapsed = (time.perf_counter() - fallback_started) * 1000
                data = fallback.json() if fallback.headers.get('content-type', '').startswith('application/json') else {}
                reply = str(data.get('reply') or '') if isinstance(data, dict) else ''
                leaks = 1 if _contains_forbidden(reply) else 0
                return ProbeResult(fallback.status_code < 500 and bool(reply), elapsed, elapsed, leaks, False, True, False, False, data.get('error_code') if isinstance(data, dict) else None)

            stream_buffer = ''
            async for chunk in response.aiter_text():
                if chunk and first_chunk_ms is None:
                    first_chunk_ms = (time.perf_counter() - started) * 1000
                stream_buffer += chunk
    except Exception as exc:
        return ProbeResult(False, 0.0, (time.perf_counter() - started) * 1000, 0, False, False, False, True, type(exc).__name__)


    for event, data in _parse_sse_events(stream_buffer):
        if event == 'reply_delta':
            visible.append(str(data.get('text') or ''))
        elif event == 'replay':
            replayed = True
            visible.append(str(data.get('reply') or ''))
        elif event == 'error':
            error_code = str(data.get('error_code') or 'stream_error')
    total_ms = (time.perf_counter() - started) * 1000
    visible_text = ''.join(visible)
    raw_leak_count = 1 if _contains_forbidden(visible_text) else 0
    return ProbeResult(
        ok=bool(visible_text) and error_code is None,
        first_chunk_ms=first_chunk_ms or total_ms,
        total_ms=total_ms,
        raw_leak_count=raw_leak_count,
        replayed=replayed,
        fallback=False,
        stream_success=error_code is None and bool(visible_text),
        stream_error=error_code is not None,
        error_code=error_code,
    )


async def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip('/')
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(connect=2.0, read=90.0, write=8.0, pool=10.0)
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async def guarded(seq: int) -> ProbeResult:
            async with sem:
                return await _probe_one(client, base_url, seq, require_stream=args.require_stream)
        results = await asyncio.gather(*(guarded(i) for i in range(args.requests)))
    success = [r for r in results if r.ok]
    errors = [r for r in results if not r.ok]
    summary = {
        'version': 'V2.2.2',
        'requests': args.requests,
        'success_count': len(success),
        'error_count': len(errors),
        'error_rate': (len(errors) / args.requests) if args.requests else 0.0,
        'p95_first_chunk_ms': _p95([r.first_chunk_ms for r in success]),
        'p95_total_ms': _p95([r.total_ms for r in success]),
        'raw_leak_count': sum(r.raw_leak_count for r in results),
        'replay_count': sum(1 for r in results if r.replayed),
        'fallback_count': sum(1 for r in results if r.fallback),
        'stream_success_count': sum(1 for r in results if r.stream_success),
        'stream_error_count': sum(1 for r in results if r.stream_error),
        'require_stream': bool(args.require_stream),
        'errors_by_code': {},
    }
    for row in errors:
        code = row.error_code or 'unknown'
        summary['errors_by_code'][code] = summary['errors_by_code'].get(code, 0) + 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='WebChat Fast Lane V2.2.2 performance and leak release gate')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--requests', type=int, default=50)
    parser.add_argument('--concurrency', type=int, default=10)
    parser.add_argument('--fail-on-gate', action='store_true')
    parser.add_argument('--require-stream', action='store_true')
    parser.add_argument('--expect-stream-disabled', action='store_true')
    parser.add_argument('--expect-stream-not-in-rollout', action='store_true')
    parser.add_argument('--force-stream-canary-header', action='store_true')
    parser.add_argument('--max-fallback-count', type=int, default=999999)
    parser.add_argument('--max-error-rate', type=float, default=0.01)
    parser.add_argument('--max-p95-first-chunk-ms', type=float, default=1500)
    parser.add_argument('--max-p95-total-ms', type=float, default=6000)
    parser.add_argument('--max-raw-leak-count', type=int, default=0)
    args = parser.parse_args()
    summary = asyncio.run(run_probe(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    failed = (
        summary['error_rate'] > args.max_error_rate
        or summary['p95_first_chunk_ms'] > args.max_p95_first_chunk_ms
        or summary['p95_total_ms'] > args.max_p95_total_ms
        or summary['raw_leak_count'] > args.max_raw_leak_count
        or summary['fallback_count'] > args.max_fallback_count
    )
    if args.fail_on_gate and failed:
        raise SystemExit(2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
