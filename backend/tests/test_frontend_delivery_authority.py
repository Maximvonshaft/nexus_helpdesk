import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def test_controlled_compose_is_the_only_runtime_topology_authority():
    controlled = (PROJECT / "deploy/docker-compose.controlled.yml").read_text()
    assert "CONTROLLED_IMAGE:?" in controlled
    assert "build:" not in controlled
    assert ":latest" not in controlled
    assert "run_worker_supervised.py" in controlled
    assert "scripts/check_worker_progress.py" in controlled


def test_source_release_packages_only_canonical_source_authorities():
    script = (ROOT / "scripts/build_source_release.sh").read_text()
    assert "nexus_canonical_source_release.zip" in script
    assert 'copy_tree "$ROOT/backend"' in script
    assert 'copy_tree "$ROOT/webapp"' in script
    assert 'copy_tree "$ROOT/config"' in script
    assert 'copy_tree "$ROOT/docs"' in script
    assert 'copy_tree "$ROOT/frontend"' not in script
    assert "ROUND" not in script


def test_frontend_routes_expose_only_canonical_product_domains():
    router = (PROJECT / "webapp/src/router.tsx").read_text()
    root = (PROJECT / "webapp/src/routes/root.tsx").read_text()
    workspace = (PROJECT / "webapp/src/routes/workspace.tsx").read_text()
    knowledge = (PROJECT / "webapp/src/routes/knowledge.tsx").read_text()
    channels = (PROJECT / "webapp/src/routes/channels.tsx").read_text()
    runtime = (PROJECT / "webapp/src/routes/runtime.tsx").read_text()
    control_tower = (PROJECT / "webapp/src/routes/control-tower.tsx").read_text()
    webchat = (PROJECT / "webapp/src/routes/webchat.tsx").read_text()
    shell = (PROJECT / "webapp/src/app/AppShell.tsx").read_text()

    for route_name in (
        "WorkspaceRoute",
        "KnowledgeRoute",
        "ChannelsRoute",
        "RuntimeRoute",
        "ControlTowerRoute",
        "WebchatRoute",
    ):
        assert route_name in router
    assert "AccountsRoute" not in router
    assert "const destination = authenticated ? '/workspace' : '/login'" in root
    assert "path: '/workspace'" in workspace
    assert "path: '/knowledge'" in knowledge
    assert "path: '/channels'" in channels
    assert "path: '/runtime'" in runtime
    assert "path: '/control-tower'" in control_tower
    assert "path: '/webchat'" in webchat
    assert "WebchatCompatibilityRedirect" in webchat
    assert "support-console" not in webchat
    assert "AppNavigation" in shell


def test_frontend_governance_is_concentrated_in_shell_transport_and_login_boundary():
    webchat = (PROJECT / "webapp/src/routes/webchat.tsx").read_text()
    api_client = (PROJECT / "webapp/src/lib/apiClient.ts").read_text()
    support_api = (PROJECT / "webapp/src/lib/supportApi.ts").read_text()
    shell = (PROJECT / "webapp/src/app/AppShell.tsx").read_text()
    channels = (PROJECT / "webapp/src/features/channels/ChannelsPage.tsx").read_text()
    runtime = (PROJECT / "webapp/src/features/runtime/RuntimePage.tsx").read_text()

    assert "redirect({ to: '/login' })" in webchat
    assert "Authorization" in api_client
    assert "clearSupportToken()" in api_client
    assert "from '@/lib/apiClient'" in support_api
    assert "fetch(" not in support_api
    assert "AppNavigation" in shell
    assert "渠道管理" in channels
    assert "系统运行" in runtime
    assert not (PROJECT / "webapp/src/features/support-console").exists()


def test_webchat_security_invariants_are_current():
    public_api = (ROOT / "app/api/webchat_public.py").read_text()
    service = (ROOT / "app/services/webchat_service.py").read_text()
    settings = (ROOT / "app/settings.py").read_text()
    widget = (ROOT / "app/static/webchat/widget.js").read_text()
    main = (ROOT / "app/main.py").read_text()

    assert "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT" in public_api
    assert '"WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must be false in "' in settings
    assert "_RATE_BUCKETS" not in service
    assert "_enforce_rate_limit" not in service
    assert re.search(r'app\.mount\(\s*["\']/static/webchat["\']', main)
    assert "visitor_token: state.visitorToken" not in widget
    assert "X-Webchat-Visitor-Token" in widget


def test_ai_config_governance_reads_are_capability_guarded():
    permissions = (ROOT / "app/services/permissions.py").read_text()
    persona = (ROOT / "app/api/persona_profiles.py").read_text()
    knowledge = (ROOT / "app/api/knowledge_items.py").read_text()

    assert 'CAP_AI_CONFIG_READ = "ai_config.read"' in permissions
    assert "def ensure_can_read_ai_configs" in permissions
    assert persona.count("ensure_can_read_ai_configs") >= 4
    assert knowledge.count("ensure_can_read_ai_configs") >= 4


def test_deployment_templates_prevent_parallel_topology_drift():
    controlled = (PROJECT / "deploy/docker-compose.controlled.yml").read_text()
    local_db = (PROJECT / "deploy/docker-compose.controlled-postgres.yml").read_text()
    external_env = (PROJECT / "deploy/.env.controlled.example").read_text()
    local_env = (PROJECT / "deploy/.env.controlled.local-postgres.example").read_text()

    for service in (
        "worker-outbound-controlled",
        "worker-background-controlled",
        "worker-webchat-ai-controlled",
        "worker-handoff-snapshot-controlled",
    ):
        assert service in controlled
        assert service not in local_db
    assert "postgres-controlled:" in local_db
    assert "sync-daemon" not in controlled
    assert "event-daemon" not in controlled
    assert "--queue all" not in controlled
    assert "env_file:" not in controlled
    assert "EXTERNAL_CHANNEL_" not in external_env
    assert "EXTERNAL_CHANNEL_" not in local_env


def test_retired_operator_products_are_absent():
    for path in (
        "frontend",
        "webapp/src/features/support-console",
        "webapp/src/shared",
    ):
        assert not (PROJECT / path).exists()
