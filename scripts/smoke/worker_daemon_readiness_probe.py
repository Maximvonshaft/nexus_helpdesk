#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DESTRUCTIVE_ARGS = {'down', 'restart', 'rm', 'kill', 'prune', 'delete', 'truncate', 'drop'}


@dataclass
class ProbeResult:
    name: str
    ok: bool
    details: dict[str, Any]


def _fail(message: str) -> None:
    print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False, indent=2))
    raise SystemExit(2)


def _guard_read_only(argv: list[str]) -> None:
    lowered = {arg.strip().lower() for arg in argv}
    forbidden = sorted(lowered & DESTRUCTIVE_ARGS)
    if forbidden:
        _fail(f'read-only probe refused destructive argument(s): {", ".join(forbidden)}')
    if os.getenv('APP_ENV', '').strip().lower() == 'production' and os.getenv('ALLOW_MUTATING_PROBE'):
        _fail('APP_ENV=production only allows read-only probe mode')


def _http_json(url: str, *, token: str | None = None, timeout: float = 5.0) -> tuple[bool, dict[str, Any]]:
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, headers=headers, method='GET')
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            try:
                data = json.loads(raw or '{}')
            except json.JSONDecodeError:
                data = {'raw': raw[:500]}
            data['_status_code'] = resp.status
            data['_elapsed_ms'] = elapsed_ms
            return 200 <= resp.status < 300, data
    except urllib.error.HTTPError as exc:
        return False, {'_status_code': exc.code, 'error': exc.reason}
    except Exception as exc:
        return False, {'error': type(exc).__name__, 'message': str(exc)[:240]}


def _compose_ps(compose_file: str) -> dict[str, Any]:
    cmd = ['docker', 'compose', '-f', compose_file, 'ps', '--format', 'json']
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'message': str(exc)[:240]}
    if proc.returncode != 0:
        return {'ok': False, 'stderr': proc.stderr[-500:]}
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {'ok': True, 'services': rows}


def _service_state(ps: dict[str, Any], service: str) -> dict[str, Any]:
    for row in ps.get('services') or []:
        if row.get('Service') == service or row.get('Name') == service or row.get('Name', '').endswith(f'-{service}-1'):
            state = str(row.get('State') or row.get('Status') or '').lower()
            return {'found': True, 'running': 'running' in state or state == 'running', 'raw': row}
    return {'found': False, 'running': False}


def main(argv: list[str]) -> int:
    _guard_read_only(argv)
    base_url = os.getenv('APP_URL', 'http://127.0.0.1:18081').rstrip('/')
    compose_file = os.getenv('COMPOSE_FILE', 'deploy/docker-compose.server.yml')
    token = os.getenv('ADMIN_BEARER_TOKEN')
    expected_openclaw = os.getenv('EXPECT_OPENCLAW_DAEMONS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}

    results: list[ProbeResult] = []

    ok, data = _http_json(f'{base_url}/healthz')
    results.append(ProbeResult('app_health', ok, data))
    ok, data = _http_json(f'{base_url}/readyz')
    results.append(ProbeResult('app_readyz', ok, data))

    ps = _compose_ps(compose_file)
    results.append(ProbeResult('compose_ps', bool(ps.get('ok')), ps))
    if ps.get('ok'):
        for service in ('app', 'worker'):
            state = _service_state(ps, service)
            results.append(ProbeResult(f'{service}_running', bool(state.get('running')), state))
        for service in ('sync-daemon', 'event-daemon'):
            state = _service_state(ps, service)
            results.append(ProbeResult(f'{service}_running', bool(state.get('running')) if expected_openclaw else True, state))

    if token:
        for name, path in (
            ('queue_summary', '/api/admin/queues/summary'),
            ('openclaw_runtime_health', '/api/admin/openclaw/runtime-health'),
        ):
            ok, data = _http_json(f'{base_url}{path}', token=token)
            results.append(ProbeResult(name, ok, data))
    else:
        results.append(ProbeResult('queue_summary', True, {'skipped': 'ADMIN_BEARER_TOKEN not set'}))
        results.append(ProbeResult('openclaw_runtime_health', True, {'skipped': 'ADMIN_BEARER_TOKEN not set'}))

    payload = {'ok': all(item.ok for item in results), 'results': [item.__dict__ for item in results]}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
