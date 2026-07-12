from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_compose_joins_external_runtime_network() -> None:
    compose = (ROOT / "deploy" / "docker-compose.candidate.yml").read_text(encoding="utf-8")

    assert "CANDIDATE_EXTERNAL_NETWORK" in compose
    assert "external: true" in compose
    assert re.search(
        r"x-candidate-app: &candidate_app\n(?:.*\n)*?  networks:\n"
        r"    - candidate\n"
        r"    - production_runtime\n",
        compose,
    )
    assert re.search(
        r"app-candidate:\n"
        r"    <<: \*candidate_app\n",
        compose,
    )
    assert re.search(
        r"networks:\n"
        r"  candidate: \{\}\n"
        r"  production_runtime:\n"
        r"    name: \$\{CANDIDATE_EXTERNAL_NETWORK:-deploy_default\}\n"
        r"    external: true\n",
        compose,
    )


def test_candidate_compose_includes_native_whatsapp_sidecar_path() -> None:
    compose = (ROOT / "deploy" / "docker-compose.candidate.yml").read_text(encoding="utf-8")
    sidecar_overlay = (ROOT / "deploy" / "docker-compose.whatsapp-sidecar.example.yml").read_text(encoding="utf-8")

    assert "worker-outbound-candidate:" in compose
    assert "whatsapp-sidecar-candidate:" in compose
    assert "python scripts/run_worker.py --worker-id worker-outbound-candidate --queue outbound" in compose
    assert 'NEXUS_BACKEND_URL: "${CANDIDATE_NEXUS_BACKEND_URL:-http://app-candidate:8080}"' in compose
    assert 'WHATSAPP_SESSION_ROOT: "/data/whatsapp-sessions"' in compose
    assert "CANDIDATE_WHATSAPP_SESSION_ROOT" in compose
    assert "http://app:8080" not in compose
    assert 'NEXUS_BACKEND_URL: "${NEXUS_BACKEND_URL:?set NEXUS_BACKEND_URL to the intended Nexus backend service}"' in sidecar_overlay


def test_candidate_env_example_is_exact_and_safe_by_default() -> None:
    env_example = (ROOT / "deploy" / ".env.candidate.example").read_text(encoding="utf-8")

    assert "CANDIDATE_APP_PORT=18082" in env_example
    assert "CANDIDATE_EXTERNAL_NETWORK=deploy_default" in env_example
    assert "CANDIDATE_WA_SIDECAR_PORT=18795" in env_example
    assert "CANDIDATE_NEXUS_BACKEND_URL=http://app-candidate:8080" in env_example
    assert "APP_VERSION=main-<git-sha-short>" in env_example
    assert "EXPECTED_MIGRATION_HEAD=<alembic-head>" in env_example
    assert "READINESS_REQUIRE_RELEASE_METADATA=true" in env_example
    assert "PRIVATE_AI_RUNTIME_TOKEN_FILE=/run/nexus/ai_runtime_token" in env_example
    assert "PRIVATE_AI_RUNTIME_DIRECT_PATH=/api/chat" in env_example
    assert "PRIVATE_AI_RUNTIME_RAG_PATH=/api/chat" in env_example
    assert "PRIVATE_AI_RUNTIME_REQUEST_SHAPE=ollama_chat" in env_example
    assert "PRIVATE_AI_RUNTIME_MAX_PROMPT_CHARS=3500" in env_example
    assert "PRIVATE_AI_RUNTIME_WARMUP_INTERVAL_SECONDS=60" in env_example
    assert "PROVIDER_RUNTIME_TIMEOUT_MS=30000" in env_example
    assert "PROVIDER_RUNTIME_PRIMARY_PROVIDER=private_ai_runtime" in env_example
    assert "PRIVATE_AI_RUNTIME_ENABLED=false" in env_example
    assert "PROVIDER_RUNTIME_CANARY_PERCENT=0" in env_example
    assert "WHATSAPP_NATIVE_ENABLED=false" in env_example
    assert "ENABLE_OUTBOUND_DISPATCH=false" in env_example
    assert "OUTBOUND_PROVIDER=disabled" in env_example
    assert "WHATSAPP_DISPATCH_MODE=disabled" in env_example
    assert "WHATSAPP_SIDECAR_URL=http://whatsapp-sidecar-candidate:18793" in env_example
    assert "WA_SIDECAR_CONNECTOR_MODE=mock" in env_example
    assert re.search(r"(?m)^WA_SIDECAR_AUTO_START_ACCOUNTS=$", env_example)
    assert "SPEEDAF_MCP_ENABLED=false" in env_example
    assert "SPEEDAF_CANCEL_ENABLED=false" in env_example


def test_candidate_compose_includes_runtime_warmer() -> None:
    compose = (ROOT / "deploy" / "docker-compose.candidate.yml").read_text(encoding="utf-8")

    assert "runtime-warmer-candidate:" in compose
    assert "python /app/scripts/smoke/warm_private_ai_runtime.py" in compose
    assert "PRIVATE_AI_RUNTIME_WARMUP_INTERVAL_SECONDS" in compose
    assert "whatsapp-sidecar-candidate" in compose


def test_candidate_whatsapp_native_gate_workflow_covers_compose_sidecar_and_backend_contracts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "candidate-whatsapp-native-gate.yml").read_text(encoding="utf-8")

    assert "docker compose" in workflow
    assert "config --quiet" in workflow
    assert "whatsapp-sidecar-candidate" in workflow
    assert "worker-outbound-candidate" in workflow
    assert "http://app-candidate:8080" in workflow
    assert "NEXUS_BACKEND_URL: http://app:8080" in workflow
    assert "WA_SIDECAR_CONNECTOR_MODE: mock" in workflow
    assert "whatsapp_sidecar_candidate_smoke.sh" in workflow
    assert "test_admin_whatsapp_native_api.py" in workflow
    assert "test_whatsapp_native_inbound_integration.py" in workflow
    assert "test_whatsapp_native_outbound_adapter.py" in workflow
    assert "test_webchat_ai_decision_runtime.py" in workflow


def test_whatsapp_sidecar_candidate_smoke_script_defaults_to_no_live_send() -> None:
    script = (ROOT / "scripts" / "smoke" / "whatsapp_sidecar_candidate_smoke.sh").read_text(encoding="utf-8")

    assert 'CHECK_SEND="${WA_SIDECAR_SMOKE_SEND:-false}"' in script
    assert 'ALLOW_LIVE_SEND="${WA_SIDECAR_ALLOW_LIVE_SEND:-false}"' in script
    assert 'WAIT_SECONDS="${WA_SIDECAR_WAIT_SECONDS:-90}"' in script
    assert "WA_SIDECAR_QR_STATE=" in script
    assert "live WhatsApp send requires WA_SIDECAR_ALLOW_LIVE_SEND=true" in script
    assert "WA_SIDECAR_SMOKE_SEND_TARGET is required" in script
    assert "WHATSAPP_SIDECAR_CANDIDATE_SMOKE_PASS=true" in script


def test_native_whatsapp_candidate_smoke_runbook_keeps_nginx_and_writes_safe() -> None:
    runbook = (ROOT / "docs" / "ops" / "NEXUS_NATIVE_WHATSAPP_CANDIDATE_SMOKE.md").read_text(encoding="utf-8")

    assert "public nginx routing" in runbook
    assert "WHATSAPP_DISPATCH_MODE=native_sidecar" in runbook
    assert "CANDIDATE_NEXUS_BACKEND_URL=http://app-candidate:8080" in runbook
    assert "SPEEDAF_CANCEL_ENABLED=false" in runbook
    assert "WA_SIDECAR_SMOKE_START_LOGIN=true" in runbook
    assert "WA_SIDECAR_ALLOW_LIVE_SEND=true" in runbook
    assert "down" in runbook
