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
        "deploy/codex_openclaw_codex_harness_adapter.py": "/app/deploy/",
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
    assert "codex_openclaw_codex_harness_adapter.py" in command_scripts
    assert command_scripts <= copied_scripts


def test_runtime_image_installs_official_openclaw_codex_cli():
    dockerfile = _read("Dockerfile")

    assert "FROM docker.io/library/node:22-bookworm-slim AS openclaw-runtime" in dockerfile
    assert "npm install -g openclaw @openclaw/codex" in dockerfile
    assert "COPY --from=openclaw-runtime /usr/local/ /usr/local/" in dockerfile
    assert "COPY --from=webapp-builder /usr/local/bin/openclaw /usr/local/bin/openclaw" not in dockerfile
    assert "COPY --from=webapp-builder /usr/local/bin/npm /usr/local/bin/npm" not in dockerfile
    assert "COPY --from=webapp-builder /usr/local/lib/node_modules /usr/local/lib/node_modules" not in dockerfile


def test_runtime_image_validates_openclaw_cli_at_build_time():
    dockerfile = _read("Dockerfile")

    assert "node --version" in dockerfile
    assert "npm --version" in dockerfile
    assert "openclaw --version" in dockerfile
    assert "npm list -g --depth=0 openclaw @openclaw/codex" in dockerfile
    assert "/usr/local/lib/node_modules/openclaw/dist/entry.mjs" in dockerfile
    assert "/usr/local/lib/node_modules/openclaw/openclaw.mjs" in dockerfile


def test_codex_private_model_runtime_uses_persistent_openclaw_home():
    dockerfile = _read("Dockerfile")
    compose = _read("deploy/docker-compose.server.yml")

    assert "/home/appuser/.openclaw" in dockerfile
    assert "chown -R appuser:appgroup /app /home/appuser" in dockerfile
    assert "codex-openclaw-home-permissions:" in compose
    assert "user: \"0:0\"" in compose
    assert "chown -R appuser:appgroup /home/appuser/.openclaw" in compose
    assert (
        "/opt/nexus_helpdesk/deploy/runtime_secrets/openclaw_codex_home:/home/appuser/.openclaw:rw"
        in compose
    )
    assert "HOME: /home/appuser" in compose
    assert "OPENCLAW_HOME: /home/appuser/.openclaw" in compose
    assert "XDG_CONFIG_HOME: /home/appuser/.openclaw" in compose
    assert "condition: service_completed_successfully" in compose


def test_codex_private_model_runtime_uses_30_second_ready_timeout():
    compose = _read("deploy/docker-compose.server.yml")
    adapter = _read("deploy/codex_openclaw_codex_harness_adapter.py")
    runbook = _read("docs/engineering/codex_chat_smoke_runbook.md")

    assert "OPENCLAW_CODEX_READY_TIMEOUT_SECONDS: ${OPENCLAW_CODEX_READY_TIMEOUT_SECONDS:-30}" in compose
    assert 'OPENCLAW_CODEX_READY_TIMEOUT_SECONDS", "30"' in adapter
    assert "OPENCLAW_CODEX_READY_TIMEOUT_SECONDS=30" in runbook


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
