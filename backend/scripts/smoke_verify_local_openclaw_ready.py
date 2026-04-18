from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def assert_contains(path: Path, needle: str):
    text = path.read_text(encoding='utf-8')
    assert needle in text, f"Expected {needle!r} in {path}"


def main() -> int:
    assert_contains(ROOT / 'backend' / '.env.local-openclaw.example', 'OPENCLAW_DEPLOYMENT_MODE=local_gateway')
    assert_contains(ROOT / 'deploy' / 'docker-compose.local-openclaw.yml', 'host.docker.internal:host-gateway')
    assert_contains(ROOT / 'scripts' / 'deploy' / 'bootstrap_local_openclaw.sh', 'check_openclaw_connectivity.py')
    assert_contains(ROOT / 'webapp' / 'src' / 'routes' / 'runtime.tsx', '检查 OpenClaw 联调')
    print('local_openclaw_ready=ok')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
