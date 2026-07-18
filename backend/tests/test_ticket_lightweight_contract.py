from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / 'webapp' / 'src' / 'features' / 'operator-workspace' / 'OperatorWorkspacePage.tsx'
WORKSPACE_CONVERSATION = ROOT / 'webapp' / 'src' / 'features' / 'operator-workspace' / 'OperatorWorkspaceConversation.tsx'
WORKSPACE_API = ROOT / 'webapp' / 'src' / 'lib' / 'operatorWorkspaceApi.ts'
E2E_SMOKE = ROOT / 'webapp' / 'e2e' / 'operator-workspace.spec.ts'


def test_ticket_lightweight_frontend_contract_files_exist():
    assert WORKSPACE.exists()
    assert WORKSPACE_CONVERSATION.exists()
    assert WORKSPACE_API.exists()
    assert E2E_SMOKE.exists()


def test_ticket_detail_first_paint_uses_queue_summary_and_bounded_source_reads():
    workspace = WORKSPACE.read_text(encoding='utf-8')
    conversation = WORKSPACE_CONVERSATION.read_text(encoding='utf-8')
    api = WORKSPACE_API.read_text(encoding='utf-8')

    assert "operatorWorkspaceApi.unifiedQueue" in workspace
    assert "operatorWorkspaceApi.conversationThread" in workspace
    assert "operatorWorkspaceApi.sourceRecord" in workspace
    assert "operatorWorkspaceApi.reply" in conversation
    assert "supportApi.ticket(" not in workspace
    assert "/api/admin/operator-queue/unified" in api
    assert "limit: '50'" in api
    assert "sourceRecord" in api
