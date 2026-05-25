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
