from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
WEBAPP = PROJECT / 'webapp'


def test_round20b_legacy_frontend_is_physically_removed():
    assert not (PROJECT / 'frontend').exists()
    assert not (WEBAPP / 'src' / 'features' / 'support-console').exists()
    assert not (WEBAPP / 'src' / 'shared' / 'ui').exists()


def test_round20b_customer_service_console_uses_one_shell_and_route_spine():
    router = (WEBAPP / 'src' / 'router.tsx').read_text(encoding='utf-8')
    shell = (WEBAPP / 'src' / 'components' / 'layout' / 'ServiceAppShell.tsx').read_text(encoding='utf-8')
    webchat = (WEBAPP / 'src' / 'routes' / 'webchat.tsx').read_text(encoding='utf-8')

    for route in ['WorkspaceRoute', 'KnowledgeRoute', 'ChannelsRoute', 'SystemRoute', 'WebchatRoute']:
        assert route in router
    assert 'Nexus 客服中心' in shell
    assert '客服工作台' in shell
    assert '知识与规则' in shell
    assert '渠道状态' in shell
    assert '系统保障' in shell
    assert "redirect({ to: getSupportToken() ? '/workspace' : '/login'" in webchat
    assert 'support-console' not in webchat


def test_round20b_customer_service_permissions_are_capability_derived():
    shell = (WEBAPP / 'src' / 'components' / 'layout' / 'ServiceAppShell.tsx').read_text(encoding='utf-8')
    workspace = (WEBAPP / 'src' / 'features' / 'operator-workspace' / 'OperatorWorkspacePage.tsx').read_text(encoding='utf-8')
    channels = (WEBAPP / 'src' / 'features' / 'service-admin' / 'ChannelsPage.tsx').read_text(encoding='utf-8')
    knowledge = (WEBAPP / 'src' / 'features' / 'service-admin' / 'KnowledgePage.tsx').read_text(encoding='utf-8')

    assert 'operator_queue.read' in shell
    assert 'ai_config.read' in shell
    assert 'channel_account.manage' in shell
    assert 'runtime.manage' in shell
    assert 'operator_queue.read' in workspace
    assert 'channel_account.manage' in channels
    assert 'ai_config.manage' in knowledge


def test_round20b_customer_service_ui_has_single_component_and_api_authority():
    architecture = (WEBAPP / 'scripts' / 'assert-frontend-convergence.mjs').read_text(encoding='utf-8')
    api_client = (WEBAPP / 'src' / 'lib' / 'apiClient.ts').read_text(encoding='utf-8')
    support_api = (WEBAPP / 'src' / 'lib' / 'supportApi.ts').read_text(encoding='utf-8')
    workspace_api = (WEBAPP / 'src' / 'lib' / 'operatorWorkspaceApi.ts').read_text(encoding='utf-8')

    assert 'legacy frontend/ must be physically deleted' in architecture
    assert 'duplicate Support Console must be deleted' in architecture
    assert 'duplicate shared/ui authority must be deleted' in architecture
    assert 'export async function apiRequest' in api_client
    assert 'apiRequest' in support_api
    assert 'apiRequest' in workspace_api
    assert 'fetch(' not in support_api
    assert 'fetch(' not in workspace_api
