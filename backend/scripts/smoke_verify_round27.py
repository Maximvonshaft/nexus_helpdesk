#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / 'webapp'
FRONTEND_DIST = ROOT / 'frontend_dist'
LEGACY_FRONTEND = ROOT / 'frontend'
SUPPORT_CONSOLE = WEBAPP / 'src' / 'features' / 'support-console'
DUPLICATE_SHARED_UI = WEBAPP / 'src' / 'shared' / 'ui'

FORBIDDEN_PUBLIC_TERMS = [
    'ExternalChannel',
    'MCP',
    ' final console',
    'Issue and customer context',
    'Human workbench',
    'Action center',
]


def assert_no_forbidden_terms(text: str, *, source: str) -> None:
    hits = [term for term in FORBIDDEN_PUBLIC_TERMS if term in text]
    if hits:
        raise AssertionError(f'{source} still exposes forbidden UI terms: {hits}')


def run_npm(*args: str) -> None:
    result = subprocess.run(['npm', *args], cwd=str(WEBAPP), capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)


def main() -> int:
    assert not LEGACY_FRONTEND.exists(), 'legacy frontend/ must be deleted'
    assert not SUPPORT_CONSOLE.exists(), 'duplicate Support Console must be deleted'
    assert not DUPLICATE_SHARED_UI.exists(), 'duplicate shared/ui must be deleted'

    run_npm('run', 'architecture')
    run_npm('run', 'build')

    index_text = (FRONTEND_DIST / 'index.html').read_text(encoding='utf-8')
    assert_no_forbidden_terms(index_text, source='frontend_dist/index.html')
    bundled_text = ''.join(path.read_text(encoding='utf-8', errors='ignore') for path in FRONTEND_DIST.rglob('*') if path.is_file())
    for forbidden in [' final console', 'Issue and customer context', 'Human workbench', 'Action center']:
        if forbidden in bundled_text:
            raise AssertionError(f'frontend_dist still exposes operator-unfriendly copy: {forbidden}')

    print('round27 single-frontend smoke verification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
