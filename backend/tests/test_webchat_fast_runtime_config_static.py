from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.server.yml"
NGINX = ROOT / "deploy" / "nginx" / "default.conf"
REQS = ROOT / "backend" / "requirements.txt"


def test_server_compose_uses_gunicorn_uvicorn_workers():
    text = COMPOSE.read_text(encoding="utf-8")

    assert "gunicorn app.main:app" in text
    assert "uvicorn.workers.UvicornWorker" in text
    assert "WEB_CONCURRENCY" in text
    assert "WEB_TIMEOUT" in text
    assert "uvicorn app.main:app --host 0.0.0.0 --port 8080" not in text


def test_backend_requirements_include_gunicorn():
    text = REQS.read_text(encoding="utf-8")

    assert "gunicorn==" in text


def test_nginx_has_fast_reply_specific_location_and_limit():
    text = NGINX.read_text(encoding="utf-8")

    assert "limit_req_zone $binary_remote_addr zone=webchat_fast" in text
    assert "upstream nexusdesk_app" in text
    assert "location = /api/webchat/fast-reply" in text
    assert "client_max_body_size 32k" in text
    assert "limit_req zone=webchat_fast burst=20 nodelay" in text
    assert "proxy_read_timeout 8s" in text
    assert "proxy_pass http://nexusdesk_app/api/webchat/fast-reply" in text
