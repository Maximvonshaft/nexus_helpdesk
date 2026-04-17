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
