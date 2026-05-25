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
    compose = _read("deploy/docker-compose.server.yml")

    assert 'const DEFAULT_MODEL = "gpt-5.5"' in env
    assert "const DEFAULT_MAX_CONCURRENCY = 6" in env
    assert "const DEFAULT_QUEUE_TIMEOUT_MS = 750" in env
    assert "CODEX_APPSERVER_MAX_CONCURRENCY: ${CODEX_APPSERVER_MAX_CONCURRENCY:-6}" in compose
    assert "CODEX_APPSERVER_QUEUE_TIMEOUT_MS: ${CODEX_APPSERVER_QUEUE_TIMEOUT_MS:-750}" in compose
    assert "ln -sf /usr/local/lib/node_modules/@openclaw/codex/node_modules/.bin/codex /usr/local/bin/codex" in dockerfile
    assert "codex --version" in dockerfile


def test_runbook_documents_webchat_flag_and_db_canary_gate():
    runbook = _read("docs/ops/CODEX_APPSERVER_RUNTIME_V3_RUNBOOK.md")

    assert "WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true" in runbook
    assert "DB canary > 0" in runbook
    assert "Canary remains 0 by default" in runbook
    assert "pilot-functional only" in runbook
    assert "12-parallel errors" in runbook


def test_runtime_exposes_terminal_wait_and_queue_taxonomy():
    metrics = _read("tools/nexus-codex-runtime/src/metrics.ts")
    server = _read("tools/nexus-codex-runtime/src/server.ts")
    thread_runner = _read("tools/nexus-codex-runtime/src/thread-runner.ts")

    assert '"terminal_wait"' in metrics
    assert "codex_queue_timeout" in server
    assert "terminalWaitMs" in thread_runner
    assert "codex_model_error" in thread_runner
    assert "codex_login_failed" in thread_runner


def test_bridge_preserves_safe_upstream_error_taxonomy():
    source = _read("deploy/codex_app_server_bridge_proxy.py")

    assert "codex_upstream_http_error" in source
    assert "SAFE_UPSTREAM_ERROR_CODES" in source
    assert "X-Nexus-Codex-Upstream-Status" in source
    assert "upstream_error" in source


def test_sla_probe_supports_required_phases_and_summary_fields():
    script = _read("scripts/probe_codex_appserver_runtime_v3_sla.sh")

    assert 'CODEX_APPSERVER_SLA_SEQUENTIAL:-20' in script
    assert 'CODEX_APPSERVER_SLA_PARALLEL_6:-6' in script
    assert 'CODEX_APPSERVER_SLA_PARALLEL_12:-12' in script
    assert "dummy_negative" in script
    assert "error_taxonomy_summary" in script
    assert "backend_seen" in script
    assert "reply_source_seen" in script
    assert "dummy_assistant_success_count" in script


def test_model_benchmark_candidates_are_documented_opt_in():
    runbook = _read("docs/ops/CODEX_APPSERVER_RUNTIME_V3_RUNBOOK.md")
    env = _read("tools/nexus-codex-runtime/src/env.ts")

    assert 'const DEFAULT_MODEL = "gpt-5.5"' in env
    assert "gpt-5.4-mini" in runbook
    assert "gpt-5.3-codex-spark" in runbook
    assert "do not change the default" in runbook.lower()


def test_canary_remains_disabled_by_default_and_fallback_not_counted_as_success():
    config = _read("backend/app/services/webchat_fast_config.py")
    rollback = _read("docs/ops/CODEX_APPSERVER_RUNTIME_V3_ROLLBACK.md")
    runbook = _read("docs/ops/CODEX_APPSERVER_RUNTIME_V3_RUNBOOK.md")

    assert 'CODEX_APP_SERVER_CANARY_PERCENT", 0' in config
    assert "Do not count fallback responses as v3 Codex success" in rollback
    assert "Canary remains 0 by default" in runbook
    assert "Do not count rollback or fallback traffic as Codex v3 success" in runbook
