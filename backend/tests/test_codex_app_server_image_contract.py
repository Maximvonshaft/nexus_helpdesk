from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_codex_app_server_proxy_scripts_are_copied_into_runtime_image():
    dockerfile = _read("Dockerfile")

    expected_copies = {
        "deploy/codex_app_server_bridge_proxy.py": "/app/deploy/",
        "deploy/codex_app_server_private_upstream_proxy.py": "/app/deploy/",
        "deploy/codex_private_reply_engine.py": "/app/deploy/",
    }
    for source, target in expected_copies.items():
        pattern = rf"^COPY\s+{re.escape(source)}\s+{re.escape(target)}\s*$"
        assert re.search(pattern, dockerfile, flags=re.MULTILINE), f"missing Dockerfile copy for {source}"


def test_codex_app_server_compose_commands_point_to_copied_scripts():
    dockerfile = _read("Dockerfile")
    compose = _read("deploy/docker-compose.server.yml")

    copied_scripts = set(re.findall(r"^COPY\s+deploy/([^\s]+)\s+/app/deploy/\s*$", dockerfile, flags=re.MULTILINE))
    command_scripts = set(re.findall(r"command:\s+python\s+/app/deploy/([^\s]+)", compose))

    assert "codex_app_server_bridge_proxy.py" in command_scripts
    assert "codex_app_server_private_upstream_proxy.py" in command_scripts
    assert "codex_private_reply_engine.py" in command_scripts
    assert command_scripts <= copied_scripts


def test_runtime_image_does_not_install_external_channel_cli():
    dockerfile = _read("Dockerfile")

    assert "FROM docker.io/library/node:22-bookworm-slim AS node-runtime" in dockerfile
    assert "npm install -g external_channel" not in dockerfile
    assert "@external-channel/codex" not in dockerfile
    assert "COPY --from=node-runtime /usr/local/ /usr/local/" in dockerfile
    assert "COPY --from=webapp-builder /usr/local/bin/external_channel /usr/local/bin/external_channel" not in dockerfile
    assert "COPY --from=webapp-builder /usr/local/bin/npm /usr/local/bin/npm" not in dockerfile
    assert "COPY --from=webapp-builder /usr/local/lib/node_modules /usr/local/lib/node_modules" not in dockerfile


def test_runtime_image_validates_node_without_external_channel_at_build_time():
    dockerfile = _read("Dockerfile")

    assert "node --version" in dockerfile
    assert "npm --version" in dockerfile
    assert "external_channel --version" not in dockerfile
    assert "npm list -g --depth=0 external_channel" not in dockerfile


def test_codex_private_model_runtime_no_longer_uses_external_channel_home():
    dockerfile = _read("Dockerfile")
    compose = _read("deploy/docker-compose.server.yml")

    assert "/home/appuser/.external_channel" not in dockerfile
    assert "codex-external_channel-home-permissions:" not in compose
    assert "external_channel_codex_home" not in compose
    assert "EXTERNAL_CHANNEL_HOME" not in compose
    assert "XDG_CONFIG_HOME: /home/appuser/.external_channel" not in compose


def test_removed_external_channel_codex_harness_is_not_referenced():
    compose = _read("deploy/docker-compose.server.yml")
    runbook = _read("docs/engineering/codex_chat_smoke_runbook.md")

    assert "codex_external_channel_codex_harness_adapter.py" not in compose
    assert "EXTERNAL_CHANNEL_CODEX_READY_TIMEOUT_SECONDS" not in compose
    assert "EXTERNAL_CHANNEL_CODEX_READY_TIMEOUT_SECONDS=30" not in runbook


def test_codex_private_model_runtime_defaults_to_private_reply_engine():
    compose = _read("deploy/docker-compose.server.yml")
    bridge = _read("deploy/codex_app_server_bridge_proxy.py")
    runbook = _read("docs/engineering/codex_chat_smoke_runbook.md")

    assert "codex-private-model-runtime:" not in compose
    assert "http://codex-private-reply-engine:18796/reply" in compose
    assert "http://codex-private-reply-engine:18796/reply" in bridge
    assert "external_channel infer model run --local" not in runbook


def test_codex_customer_facing_hot_path_uses_low_latency_defaults():
    compose = _read("deploy/docker-compose.server.yml")
    adapter = _read("backend/app/services/provider_runtime/adapters/codex_app_server.py")
    bridge = _read("deploy/codex_app_server_bridge_proxy.py")

    assert "CODEX_APP_SERVER_AUTH_MODE: ${CODEX_APP_SERVER_AUTH_MODE:-per_request}" in compose
    assert "CODEX_APP_SERVER_LEGACY_LOGIN_STATE_ENABLED: ${CODEX_APP_SERVER_LEGACY_LOGIN_STATE_ENABLED:-false}" in compose
    assert "CODEX_APP_SERVER_TOTAL_TIMEOUT_MS: ${CODEX_APP_SERVER_TOTAL_TIMEOUT_MS:-10000}" in compose
    assert "CODEX_APP_SERVER_CONNECT_TIMEOUT_MS: ${CODEX_APP_SERVER_CONNECT_TIMEOUT_MS:-250}" in compose
    assert "CODEX_APP_SERVER_RUNTIME_BACKEND: ${CODEX_APP_SERVER_RUNTIME_BACKEND:-python_cli_pool}" in compose
    assert "CODEX_APP_SERVER_REAL_UPSTREAM_URL_PYTHON: ${CODEX_APP_SERVER_REAL_UPSTREAM_URL_PYTHON:-http://codex-private-reply-engine:18796/reply}" in compose
    assert "CODEX_APP_SERVER_REAL_UPSTREAM_URL_NODE: ${CODEX_APP_SERVER_REAL_UPSTREAM_URL_NODE:-http://codex-appserver-runtime:18810/reply}" in compose
    assert "CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS: ${CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS:-9}" in compose

    assert "_get_bridge_readyz" in adapter
    assert "bridge_readyz = await self._get_bridge_readyz" not in adapter
    assert 'CODEX_APP_SERVER_TOTAL_TIMEOUT_MS", 10000' in adapter
    assert 'CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS", "9"' in bridge


def test_codex_services_expose_release_metadata_for_image_consistency():
    bridge = _read("deploy/codex_app_server_bridge_proxy.py")

    for source in (bridge,):
        assert 'GIT_SHA = os.environ.get("GIT_SHA", "unknown")' in source
        assert 'IMAGE_TAG = os.environ.get("IMAGE_TAG", "unknown")' in source
        assert 'APP_VERSION = os.environ.get("APP_VERSION", "unknown")' in source
        assert '"git_sha": GIT_SHA' in source
        assert '"image_tag": IMAGE_TAG' in source
        assert '"app_version": APP_VERSION' in source


def test_codex_private_reply_engine_uses_30_second_ready_timeout():
    compose = _read("deploy/docker-compose.server.yml")
    engine = _read("deploy/codex_private_reply_engine.py")
    runbook = _read("docs/engineering/codex_chat_smoke_runbook.md")

    assert (
        "CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS: "
        "${CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS:-30}"
    ) in compose
    assert 'CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS", "30"' in engine
    assert "min(READYZ_TIMEOUT_SECONDS, 60.0)" in engine
    assert "min(READYZ_TIMEOUT_SECONDS, 5.0)" not in engine
    assert "CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS=30" in runbook


def test_codex_app_server_upstream_and_bridge_use_30_second_ready_timeout():
    compose = _read("deploy/docker-compose.server.yml")
    private_upstream = _read("deploy/codex_app_server_private_upstream_proxy.py")
    bridge = _read("deploy/codex_app_server_bridge_proxy.py")
    runbook = _read("docs/engineering/codex_chat_smoke_runbook.md")

    assert (
        "CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS: "
        "${CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS:-30}"
    ) in compose
    assert "CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS: ${CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS:-30}" in compose
    assert 'CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS", "30"' in private_upstream
    assert 'CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS", "30"' in bridge
    assert "min(READYZ_TIMEOUT_SECONDS, 60.0)" in private_upstream
    assert "min(READYZ_TIMEOUT_SECONDS, 60.0)" in bridge
    assert "min(READYZ_TIMEOUT_SECONDS, 5.0)" not in private_upstream
    assert "min(READYZ_TIMEOUT_SECONDS, 5.0)" not in bridge
    assert "CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS=30" in runbook
    assert "CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS=30" in runbook


def test_codex_no_traffic_smoke_runbook_waits_for_chain_readiness_in_order():
    runbook = _read("docs/engineering/codex_chat_smoke_runbook.md")

    assert "READY_WAIT_DEADLINE_SECONDS=180" in runbook
    assert "wait_readyz()" in runbook
    assert "readyz_timeout service=$service" in runbook
    assert "--max-time 35" in runbook
    assert "Do not run the admin nonce smoke until the readiness waits pass in this order" in runbook

    expected_order = [
        "wait_readyz codex-private-reply-engine http://127.0.0.1:18796/readyz",
        "wait_readyz codex-app-server-upstream http://127.0.0.1:18795/readyz",
        "wait_readyz codex-app-server-bridge http://127.0.0.1:18794/readyz",
    ]
    positions = [runbook.index(entry) for entry in expected_order]
    assert positions == sorted(positions)
