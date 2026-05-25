from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_node_runtime_package_contains_required_modules():
    base = ROOT / "tools" / "nexus-codex-runtime" / "src"
    for name in [
        "server.ts",
        "rpc-client.ts",
        "appserver-process.ts",
        "account-login.ts",
        "client-cache.ts",
        "thread-runner.ts",
        "terminal-turn-collector.ts",
        "notification-correlation.ts",
        "prompt-compiler.ts",
        "reply-contract.ts",
        "redaction.ts",
        "deadline.ts",
        "errors.ts",
        "metrics.ts",
        "env.ts",
    ]:
        assert (base / name).exists(), name


def test_no_private_openclaw_imports_or_infer_cli():
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "tools" / "nexus-codex-runtime").rglob("*.ts"))

    assert "extensions/codex/src/app-server" not in runtime
    assert "openclaw infer model run" not in runtime
    assert "from \"openclaw" not in runtime
    assert "from 'openclaw" not in runtime


def test_compose_adds_node_runtime_and_keeps_python_rollback():
    compose = _read("deploy/docker-compose.server.yml")

    assert "codex-appserver-runtime:" in compose
    assert "CODEX_APPSERVER_PORT: \"18810\"" in compose
    assert "CODEX_APPSERVER_MODEL: ${CODEX_APPSERVER_MODEL:-gpt-5.5}" in compose
    assert "codex-private-model-runtime:" in compose
    assert "PORT: \"18800\"" in compose
    assert "CODEX_APP_SERVER_RUNTIME_BACKEND" in compose
    assert "CODEX_APP_SERVER_REAL_UPSTREAM_URL_PYTHON" in compose
    assert "CODEX_APP_SERVER_REAL_UPSTREAM_URL_NODE" in compose


def test_bridge_has_runtime_backend_switch():
    source = _read("deploy/codex_app_server_bridge_proxy.py")

    assert "CODEX_APP_SERVER_RUNTIME_BACKEND" in source
    assert "python_cli_pool" in source
    assert "node_appserver" in source
    assert "codex-private-model-runtime:18800/reply" in source
    assert "codex-appserver-runtime:18810/reply" in source


def test_node_runtime_defaults_match_validated_server_profile():
    env = _read("tools/nexus-codex-runtime/src/env.ts")
    dockerfile = _read("Dockerfile")

    assert 'model: env.CODEX_APPSERVER_MODEL || "gpt-5.5"' in env
    assert "ln -sf /usr/local/lib/node_modules/@openclaw/codex/node_modules/.bin/codex /usr/local/bin/codex" in dockerfile
    assert "codex --version" in dockerfile


def test_runbook_documents_webchat_flag_and_db_canary_gate():
    runbook = _read("docs/ops/CODEX_APPSERVER_RUNTIME_V3_RUNBOOK.md")

    assert "WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true" in runbook
    assert "DB canary > 0" in runbook
    assert "Canary remains 0 by default" in runbook
    assert "pilot-functional only" in runbook
    assert "12-parallel errors" in runbook
