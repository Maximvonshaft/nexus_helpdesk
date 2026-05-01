from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def test_compose_image_tags_are_aligned_to_current_release():
    compose = (PROJECT / 'deploy' / 'docker-compose.cloud.yml').read_text()
    assert compose.count('nexusdesk/helpdesk:round20b') == 4 or compose.count('nexusdesk/helpdesk:round27') == 4
    assert 'round26' not in compose


def test_source_release_script_defaults_to_current_release_and_includes_current_report():
    script = (ROOT / 'scripts' / 'build_source_release.sh').read_text()
    assert 'helpdesk_suite_lite_round20B_source_release.zip' in script or 'helpdesk_suite_lite_round27_source_release.zip' in script
    assert 'ROUND20B_LEGACY_PRODUCTION_REPORT.md' in script or 'ROUND27_FRONTEND_OPERATOR_HARDENING_REPORT.md' in script


def test_frontend_routes_are_role_gated_for_operator_simplicity():
    shell = (PROJECT / 'webapp' / 'src' / 'layouts' / 'AppShell.tsx').read_text()
    runtime = (PROJECT / 'webapp' / 'src' / 'routes' / 'runtime.tsx').read_text()
    accounts = (PROJECT / 'webapp' / 'src' / 'routes' / 'accounts.tsx').read_text()
    command = (PROJECT / 'webapp' / 'src' / 'components' / 'ui' / 'CommandPalette.tsx').read_text()

    assert 'roleWorkspaceHint' in shell
    assert 'canViewOps' in shell
    assert 'canManageChannels' in shell
    assert '运营保障' in runtime
    assert '无权限访问' in runtime
    assert '发送线路' in accounts
    assert '无权限访问' in accounts
    assert "permission: 'ops'" in command
    assert "permission: 'channels'" in command


def test_frontend_governance_gates_use_capabilities_not_roles():
    access = (PROJECT / 'webapp' / 'src' / 'lib' / 'access.ts').read_text()
    assert "return hasCapability(user, CAP_RUNTIME_MANAGE)" in access
    assert "return hasCapability(user, CAP_CHANNEL_ACCOUNT_MANAGE)" in access
    assert "return hasCapability(user, CAP_USER_MANAGE)" in access
    assert "return hasCapability(user, CAP_MARKET_MANAGE)" in access
    assert "isOpsSupervisorRole(user?.role) || hasCapability" not in access
    assert "CAP_AI_CONFIG_READ" in access


def test_webchat_governance_hardening_invariants():
    api = (ROOT / 'app' / 'api' / 'webchat.py').read_text()
    service = (ROOT / 'app' / 'services' / 'webchat_service.py').read_text()
    settings = (ROOT / 'app' / 'settings.py').read_text()
    widget = (ROOT / 'app' / 'static' / 'webchat' / 'widget.js').read_text()
    main = (ROOT / 'app' / 'main.py').read_text()

    assert 'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT' in api
    assert 'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must be false in production' in settings
    assert '_RATE_BUCKETS' not in service
    assert '_enforce_rate_limit' not in service
    assert "app.mount('/static/webchat'" in main
    assert 'NEXUSDESK ROUND B WEBCHAT STATIC HOTFIX' not in main
    assert "visitor_token: state.visitorToken" not in widget
    assert "X-Webchat-Visitor-Token" in widget


def test_ai_config_governance_reads_are_capability_guarded():
    permissions = (ROOT / 'app' / 'services' / 'permissions.py').read_text()
    persona = (ROOT / 'app' / 'api' / 'persona_profiles.py').read_text()
    knowledge = (ROOT / 'app' / 'api' / 'knowledge_items.py').read_text()

    assert 'CAP_AI_CONFIG_READ = "ai_config.read"' in permissions
    assert 'def ensure_can_read_ai_configs' in permissions
    assert persona.count('ensure_can_read_ai_configs') >= 4
    assert knowledge.count('ensure_can_read_ai_configs') >= 4


def test_ci_readiness_checks_are_blocking():
    backend_ci = (PROJECT / '.github' / 'workflows' / 'backend-ci.yml').read_text()
    pg_ci = (PROJECT / '.github' / 'workflows' / 'postgres-migration.yml').read_text()

    assert 'validate_production_readiness.py || true' not in backend_ci
    assert 'validate_production_readiness.py || true' not in pg_ci
    assert 'Strict readiness' in backend_ci
    assert 'Strict production-like readiness' in pg_ci


def test_deployment_templates_prevent_server_drift():
    server_compose = (PROJECT / 'deploy' / 'docker-compose.server.example.yml').read_text()
    env_template = (PROJECT / 'deploy' / '.env.prod.example').read_text()
    readme = (PROJECT / 'README.md').read_text()

    assert 'sync-daemon' in server_compose
    assert 'event-daemon' in server_compose
    assert 'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT: "false"' in server_compose
    assert 'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=false' in env_template
    assert 'Server deployment drift prevention' in readme
    assert 'git reset --hard' in readme


def test_legacy_frontend_copy_is_business_friendly():
    legacy_index = (PROJECT / 'frontend' / 'index.html').read_text()
    legacy_app = (PROJECT / 'frontend' / 'app.js').read_text()

    assert '客服工作台 final console' not in legacy_index
    assert 'Issue and customer context' not in legacy_index
    assert 'Human workbench' not in legacy_index
    assert 'Action center' not in legacy_index
    assert 'Auto inject to AI' not in legacy_index
    assert 'Go to overview' not in legacy_app
    assert 'Refresh all data' not in legacy_app
    assert 'Issue summary and customer request are required' not in legacy_app


def test_round27_smoke_script_exists_and_checks_public_copy():
    script = (ROOT / 'scripts' / 'smoke_verify_round27.py').read_text()
    assert 'FORBIDDEN_PUBLIC_TERMS' in script
    assert 'npm' in script and 'build' in script
    assert 'frontend_dist' in script
