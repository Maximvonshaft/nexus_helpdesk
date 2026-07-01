from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_server_compose_is_parameterized_for_canonical_production_turnup() -> None:
    compose = read("deploy/docker-compose.server.yml")
    env_example = read("deploy/.env.prod.example")

    assert "127.0.0.1:${APP_HOST_PORT:-18081}:8080" in compose
    assert "${NEXUSDESK_RUNTIME_SECRETS_DIR:-/opt/nexus_helpdesk/deploy/runtime_secrets}" in compose
    assert "${NEXUSDESK_UPLOADS_DIR:-/opt/nexus_helpdesk/data/uploads}:/app/backend/uploads" in compose
    assert "EXTERNAL_CHANNEL_TRANSPORT: disabled" in compose
    assert "EXTERNAL_CHANNEL_DEPLOYMENT_MODE: disabled" in compose
    assert "EXTERNAL_CHANNEL_SYNC_ENABLED: \"false\"" in compose
    assert "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED: \"false\"" in compose
    assert "NEXUSDESK_RUNTIME_SECRETS_DIR=/opt/nexus_helpdesk/deploy/runtime_secrets" in env_example
    assert "NEXUSDESK_UPLOADS_DIR=/opt/nexus_helpdesk/data/uploads" in env_example


def test_public_production_smoke_workflow_is_manual_and_secret_free() -> None:
    workflow = read(".github/workflows/public-production-smoke.yml")
    script = read("scripts/smoke/public_webchat_smoke.py")

    assert "workflow_dispatch:" in workflow
    assert "base_url:" in workflow
    assert "expected_git_sha:" in workflow
    assert "expected_image_tag:" in workflow
    assert "require_ai_reply:" in workflow
    assert "scripts/smoke/public_webchat_smoke.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "/healthz" in script
    assert "/readyz" in script
    assert "/webchat/demo/" in script
    assert "/api/webchat/fast-reply" in script
    assert "reply_starts_json" in script
    assert "fast_reply_handoff_required" in script
    assert "PUBLIC_WEBCHAT_SMOKE_PASS=true" in script
    assert "Authorization" not in script
    assert "Bearer " not in script


def test_drift_audit_does_not_dump_token_bearing_nginx_config() -> None:
    script = read("scripts/deploy/audit_production_drift.sh")

    assert "nginx -T" not in script
    assert "proxy_pass" not in script
    assert "127\\.0\\.0\\.1:[0-9]+" in script
    assert "PRODUCTION_DRIFT_AUDIT_PASS=true" in script
    assert "keep" in script
    assert "rewrite" in script
    assert "drop" in script


def test_prepare_production_release_env_is_non_mutating_by_default() -> None:
    script = read("scripts/deploy/prepare_production_release_env.sh")

    assert "refusing in-place update" in script
    assert "ALLOW_IN_PLACE=true" in script
    assert "PRODUCTION_RELEASE_ENV_PREPARED=true" in script
    assert "release metadata must not contain secret-like keys" in script
    assert "does not run docker compose, reload nginx, or change public traffic" in script
