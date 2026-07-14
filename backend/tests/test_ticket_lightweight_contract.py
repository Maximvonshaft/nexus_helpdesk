from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / 'webapp' / 'src' / 'features' / 'operator-workspace' / 'OperatorWorkspacePage.tsx'
WORKSPACE_API = ROOT / 'webapp' / 'src' / 'lib' / 'operatorWorkspaceApi.ts'
API_CLIENT = ROOT / 'webapp' / 'src' / 'lib' / 'apiClient.ts'
E2E_SMOKE = ROOT / 'webapp' / 'e2e' / 'smoke.spec.ts'


def test_ticket_lightweight_frontend_contract_files_exist():
    assert WORKSPACE.exists()
    assert WORKSPACE_API.exists()
    assert API_CLIENT.exists()
    assert E2E_SMOKE.exists()
    assert not (ROOT / 'webapp' / 'src' / 'features' / 'support-console').exists()


def test_ticket_workspace_uses_scoped_summary_and_thread_endpoints_not_heavy_ticket_page():
    workspace = WORKSPACE.read_text(encoding='utf-8')
    api = WORKSPACE_API.read_text(encoding='utf-8')
    client = API_CLIENT.read_text(encoding='utf-8')

    assert 'operatorWorkspaceApi.unifiedQueue' in workspace
    assert 'operatorWorkspaceApi.conversationThread' in workspace
    assert 'operatorWorkspaceApi.sourceRecord' in workspace
    assert '/api/admin/operator-queue/unified' in api
    assert 'requireApiPath: true' in api
    assert 'apiRequest' in api
    assert 'export async function apiRequest' in client
    assert 'supportApi.ticket(' not in workspace
