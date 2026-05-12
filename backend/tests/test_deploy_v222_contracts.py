from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'deploy' / 'docker-compose.server.yml'
NGINX = ROOT / 'deploy' / 'nginx' / 'default.conf'


def test_compose_has_isolated_worker_services_and_profiles():
    text = COMPOSE.read_text(encoding='utf-8')
    assert 'worker-outbound:' in text
    assert 'worker-background:' in text
    assert 'worker-handoff-snapshot:' in text
    assert 'worker-webchat-ai:' in text
    assert 'worker-openclaw-inbound:' in text
    assert 'profiles:' in text
    assert '- openclaw-inbound' in text
    assert 'legacy-worker:' in text
    assert '- legacy-worker' in text
    assert 'app:' in text


def test_compose_worker_queue_commands_are_exact():
    text = COMPOSE.read_text(encoding='utf-8')
    assert '--queue outbound' in text
    assert '--queue background' in text
    assert '--queue handoff-snapshot' in text
    assert '--queue webchat-ai' in text
    assert '--queue openclaw-inbound' in text
    assert '--queue all' in text


def test_nginx_stream_contract_has_exact_stream_route_and_timeouts():
    text = NGINX.read_text(encoding='utf-8')
    assert 'location = /api/webchat/fast-reply/stream' in text
    assert 'proxy_buffering off;' in text
    assert 'proxy_cache off;' in text
    assert 'proxy_read_timeout 60s;' in text
    assert 'proxy_connect_timeout 1s;' in text
    assert 'proxy_send_timeout 8s;' in text
    assert 'X-Accel-Buffering "no"' in text
    assert 'Cache-Control "no-store"' in text
    assert 'limit_req zone=webchat_fast burst=20 nodelay;' in text
    assert 'client_max_body_size 32k;' in text
