#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / 'webapp'
FRONTEND = ROOT / 'frontend'
ACCESS = ROOT / 'webapp' / 'src' / 'lib' / 'access.ts'
API = ROOT / 'webapp' / 'src' / 'lib' / 'api.ts'
WORKSPACE = ROOT / 'webapp' / 'src' / 'routes' / 'workspace.tsx'
INIT_DB = ROOT / 'backend' / 'scripts' / 'init_dev_db.py'


def assert_contains(path: Path, needle: str) -> None:
    text = path.read_text(encoding='utf-8')
    if needle not in text:
        raise AssertionError(f'{path} missing required marker: {needle}')


def assert_not_contains(path: Path, needle: str) -> None:
    text = path.read_text(encoding='utf-8')
    if needle in text:
        raise AssertionError(f'{path} still contains forbidden marker: {needle}')


def main() -> int:
    assert_contains(ACCESS, "['admin', 'manager'].includes(normalized)")
    assert_contains(ACCESS, 'canEditBulletins')
    assert_contains(API, '/api/lookups/markets')
    assert_contains(API, '/api/lookups/bulletins')
    assert_contains(WORKSPACE, '来源状态')
    assert_not_contains(WORKSPACE, '会话编号')
    assert_contains(INIT_DB, 'db.commit()')
    assert_contains(INIT_DB, 'MarketBulletin(')
    assert_not_contains(FRONTEND / 'app.js', '会话编号')

    result = subprocess.run(['npm', 'run', 'build'], cwd=str(WEBAPP), capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)

    print('round20A smoke verification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
