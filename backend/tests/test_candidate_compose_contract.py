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
    assert "ENABLE_OUTBOUND_DISPATCH: ${ENABLE_OUTBOUND_DISPATCH:-false}" in compose
    assert "OUTBOUND_PROVIDER: ${OUTBOUND_PROVIDER:-native}" in compose
    assert "WHATSAPP_NATIVE_ENABLED: ${WHATSAPP_NATIVE_ENABLED:-true}" in compose
    assert "WHATSAPP_DISPATCH_MODE: ${WHATSAPP_DISPATCH_MODE:-native_sidecar}" in compose
    assert "EXTERNAL_CHANNEL_BRIDGE_ENABLED: ${EXTERNAL_CHANNEL_BRIDGE_ENABLED:-false}" in compose
    assert "EXTERNAL_CHANNEL_TRANSPORT: ${EXTERNAL_CHANNEL_TRANSPORT:-disabled}" in compose
    assert 'NEXUS_BACKEND_URL: "${CANDIDATE_NEXUS_BACKEND_URL:-http://app-candidate:8080}"' in compose
    assert 'WHATSAPP_SESSION_ROOT: "/data/whatsapp-sessions"' in compose
    assert "CANDIDATE_WHATSAPP_SESSION_ROOT" in compose
    assert "WA_SIDECAR_AUTO_START_ACCOUNTS" in compose
    assert "WA_SIDECAR_BROWSER_PLATFORM" in compose
    assert "WA_SIDECAR_OPERATION_TIMEOUT_MS" in compose
    assert "WA_SIDECAR_RECONNECT_MAX_ATTEMPTS" in compose
    assert "WA_SIDECAR_BAILEYS_LOG_LEVEL" in compose
    assert "http://app:8080" not in compose
    assert 'NEXUS_BACKEND_URL: "${NEXUS_BACKEND_URL:?set NEXUS_BACKEND_URL to the intended Nexus backend service}"' in sidecar_overlay
    assert "WA_SIDECAR_AUTO_START_ACCOUNTS" in sidecar_overlay
    assert "WA_SIDECAR_BROWSER_PLATFORM" in sidecar_overlay
    assert "WA_SIDECAR_OPERATION_TIMEOUT_MS" in sidecar_overlay
    assert "WA_SIDECAR_RECONNECT_MAX_ATTEMPTS" in sidecar_overlay
    assert "WA_SIDECAR_BAILEYS_LOG_LEVEL" in sidecar_overlay


def test_candidate_env_example_documents_external_network() -> None:
    env_example = (ROOT / "deploy" / ".env.candidate.example").read_text(encoding="utf-8")

    assert "CANDIDATE_APP_PORT=18082" in env_example
    assert "CANDIDATE_EXTERNAL_NETWORK=deploy_default" in env_example
    assert "CANDIDATE_WA_SIDECAR_PORT=18795" in env_example
    assert "CANDIDATE_NEXUS_BACKEND_URL=http://app-candidate:8080" in env_example
    assert "ENABLE_OUTBOUND_DISPATCH=false" in env_example
    assert "OUTBOUND_PROVIDER=native" in env_example
    assert "EXTERNAL_CHANNEL_BRIDGE_ENABLED=false" in env_example
    assert "EXTERNAL_CHANNEL_TRANSPORT=disabled" in env_example
    assert "EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled" in env_example
    assert "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED=false" in env_example
    assert "EXTERNAL_CHANNEL_SYNC_ENABLED=false" in env_example
    assert "EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED=false" in env_example
    assert "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED=false" in env_example
    assert ("open" + "claw") not in env_example.lower()
    assert "PRIVATE_AI_RUNTIME_TOKEN_FILE=/run/nexus/ai_runtime_token" in env_example
    assert "PRIVATE_AI_RUNTIME_REQUEST_SHAPE=question" in env_example
    assert "PRIVATE_AI_RUNTIME_MAX_PROMPT_CHARS=1200" in env_example
    assert "PRIVATE_AI_RUNTIME_TRACKING_MISSING_FAST_PATH_ENABLED=false" in env_example
    assert "WEBCHAT_FAST_AI_FALLBACK_PROVIDER=none" in env_example
    assert "PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]" in env_example
    assert "PROVIDER_RUNTIME_TIMEOUT_MS=60000" in env_example
    assert "PROVIDER_RUNTIME_PRIMARY_PROVIDER=private_ai_runtime" in env_example
    assert "WHATSAPP_NATIVE_ENABLED=true" in env_example
    assert "WHATSAPP_DISPATCH_MODE=native_sidecar" in env_example
    assert "WHATSAPP_SIDECAR_URL=http://whatsapp-sidecar-candidate:18793" in env_example
    assert "WA_SIDECAR_AUTO_START_ACCOUNTS=wa-main" in env_example
    assert "WA_SIDECAR_BROWSER_PLATFORM=Ubuntu" in env_example
    assert "WA_SIDECAR_OPERATION_TIMEOUT_MS=60000" in env_example
    assert "WA_SIDECAR_RECONNECT_MAX_ATTEMPTS=20" in env_example
    assert "WA_SIDECAR_BAILEYS_LOG_LEVEL=silent" in env_example
    assert "SPEEDAF_MCP_ENABLED=false" in env_example
    assert "SPEEDAF_CANCEL_ENABLED=false" in env_example


def test_candidate_whatsapp_native_gate_workflow_covers_compose_sidecar_and_backend_contracts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "candidate-whatsapp-native-gate.yml").read_text(encoding="utf-8")

    assert "docker compose" in workflow
    assert "config --quiet" in workflow
    assert "whatsapp-sidecar-candidate" in workflow
    assert "worker-outbound-candidate" in workflow
    assert "http://app-candidate:8080" in workflow
    assert "NEXUS_BACKEND_URL: http://app:8080" in workflow
    assert "WA_SIDECAR_AUTO_START_ACCOUNTS" in workflow
    assert "WA_SIDECAR_BROWSER_PLATFORM" in workflow
    assert "WA_SIDECAR_OPERATION_TIMEOUT_MS" in workflow
    assert "WA_SIDECAR_RECONNECT_MAX_ATTEMPTS" in workflow
    assert "WA_SIDECAR_BAILEYS_LOG_LEVEL" in workflow
    assert "WA_SIDECAR_CONNECTOR_MODE: mock" in workflow
    assert "whatsapp_candidate_runtime_audit.sh" in workflow
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
    assert 'DEFAULT_ACCOUNT_LIST="${WA_SIDECAR_AUTO_START_ACCOUNTS:-}"' in script
    assert "WA_SIDECAR_QR_STATE=" in script
    assert "live WhatsApp send requires WA_SIDECAR_ALLOW_LIVE_SEND=true" in script
    assert "WA_SIDECAR_SMOKE_SEND_TARGET is required" in script
    assert "WHATSAPP_SIDECAR_CANDIDATE_SMOKE_PASS=true" in script


def test_whatsapp_candidate_runtime_audit_blocks_retired_runtime_drift() -> None:
    script = (ROOT / "scripts" / "smoke" / "whatsapp_candidate_runtime_audit.sh").read_text(encoding="utf-8")

    assert "WHATSAPP_CANDIDATE_RUNTIME_AUDIT_PASS=true" in script
    assert "retired vendor" in script.lower()
    assert ("open" + "claw") not in script.lower()
    assert '"OUTBOUND_PROVIDER": "native"' in script
    assert '"WHATSAPP_DISPATCH_MODE": "native_sidecar"' in script
    assert '"EXTERNAL_CHANNEL_BRIDGE_ENABLED": "false"' in script
    assert "NEXUS_BACKEND_URL: http://app:8080" in script
    assert "WA_CANDIDATE_AUDIT_RUNNING_CONTAINERS" in script


def test_native_whatsapp_candidate_smoke_runbook_keeps_nginx_and_writes_safe() -> None:
    runbook = (ROOT / "docs" / "ops" / "NEXUS_NATIVE_WHATSAPP_CANDIDATE_SMOKE.md").read_text(encoding="utf-8")

    assert "public nginx routing" in runbook
    assert "WHATSAPP_DISPATCH_MODE=native_sidecar" in runbook
    assert "OUTBOUND_PROVIDER=native" in runbook
    assert "ENABLE_OUTBOUND_DISPATCH=false" in runbook
    assert "whatsapp_candidate_runtime_audit.sh" in runbook
    assert "CANDIDATE_NEXUS_BACKEND_URL=http://app-candidate:8080" in runbook
    assert "SPEEDAF_CANCEL_ENABLED=false" in runbook
    assert "WA_SIDECAR_SMOKE_START_LOGIN=true" in runbook
    assert "WA_SIDECAR_ALLOW_LIVE_SEND=true" in runbook
    assert "down" in runbook
