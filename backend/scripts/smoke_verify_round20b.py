#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / 'webapp'
FRONTEND = ROOT / 'frontend'
APP = FRONTEND / 'app.js'
HTML = FRONTEND / 'index.html'
COMPOSE = ROOT / 'deploy' / 'docker-compose.cloud.yml'
BUILD = ROOT / 'backend' / 'scripts' / 'build_source_release.sh'


def assert_contains(path: Path, needle: str) -> None:
    text = path.read_text(encoding='utf-8')
    if needle not in text:
        raise AssertionError(f'{path} missing required marker: {needle}')


def assert_not_contains(path: Path, needle: str) -> None:
    text = path.read_text(encoding='utf-8')
    if needle in text:
        raise AssertionError(f'{path} still contains forbidden marker: {needle}')


def main() -> int:
    assert_contains(APP, "api('/lookups/bulletins')")
    assert_contains(APP, "api('/lookups/markets')")
    assert_contains(APP, 'function applyRoleAccess()')
    assert_contains(APP, "当前账号无需查看运营保障")
    assert_contains(HTML, 'overview-metric-label-1')
    assert_not_contains(HTML, 'api-base-url')
    assert_contains(HTML, 'bulletin-readonly-note')
    assert_contains(HTML, 'account-readonly-note')
    assert_contains(HTML, 'sidebar-role-hint')
    assert_contains(BUILD, 'helpdesk_suite_lite_round20B_source_release.zip')
    assert_contains(COMPOSE, 'nexusdesk/helpdesk:round20b')

    result = subprocess.run(['npm', 'run', 'build'], cwd=str(WEBAPP), capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)

    print('round20B smoke verification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
