#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / 'webapp'
FRONTEND_DIST = ROOT / 'frontend_dist'
LEGACY_INDEX = ROOT / 'frontend' / 'index.html'
LEGACY_APP = ROOT / 'frontend' / 'app.js'

FORBIDDEN_PUBLIC_TERMS = [
    'OpenClaw',
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


def main() -> int:
    legacy_index = LEGACY_INDEX.read_text(encoding='utf-8')
    legacy_app = LEGACY_APP.read_text(encoding='utf-8')
    assert '客服工作台' in legacy_index
    assert '运营保障' in legacy_index
    assert_no_forbidden_terms(legacy_index, source='frontend/index.html')

    for forbidden in ['Issue summary and customer request are required', 'Go to overview', 'Refresh all data']:
        if forbidden in legacy_app:
            raise AssertionError(f'frontend/app.js still has untranslated operator copy: {forbidden}')

    result = subprocess.run(['npm', 'run', 'build'], cwd=str(WEBAPP), capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)

    index_text = (FRONTEND_DIST / 'index.html').read_text(encoding='utf-8')
    assert_no_forbidden_terms(index_text, source='frontend_dist/index.html')
    bundled_text = ''.join(path.read_text(encoding='utf-8', errors='ignore') for path in FRONTEND_DIST.rglob('*') if path.is_file())
    for forbidden in [' final console', 'Issue and customer context', 'Human workbench', 'Action center']:
        if forbidden in bundled_text:
            raise AssertionError(f'frontend_dist still exposes operator-unfriendly copy: {forbidden}')

    print('round27 smoke verification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
