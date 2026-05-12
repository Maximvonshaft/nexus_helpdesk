from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / 'webapp' / 'src' / 'routes' / 'workspace.tsx'
API = ROOT / 'webapp' / 'src' / 'lib' / 'api.ts'
NODE_TEST = ROOT / 'webapp' / 'tests' / 'workspace-ticket-detail.test.mjs'


def test_ticket_lightweight_frontend_contract_files_exist():
    assert WORKSPACE.exists()
    assert API.exists()
    assert NODE_TEST.exists()


def test_ticket_detail_first_paint_uses_summary_and_timeline_not_heavy_endpoint():
    workspace = WORKSPACE.read_text(encoding='utf-8')
    api = API.read_text(encoding='utf-8')
    assert "api.caseDetail(selectedId as number)" in workspace
    assert "api.ticketTimeline(selectedId as number, { limit: 50 })" in workspace
    assert "api.ticket(" not in workspace
    assert "`/api/tickets/${ticketId}/summary`" in api
    assert "`/api/tickets/${ticketId}/timeline?${search.toString()}`" in api
