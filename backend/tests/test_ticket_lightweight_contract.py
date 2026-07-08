from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPORT_CONSOLE = ROOT / 'webapp' / 'src' / 'features' / 'support-console' / 'SupportConsolePage.tsx'
SUPPORT_API = ROOT / 'webapp' / 'src' / 'lib' / 'supportApi.ts'
E2E_SMOKE = ROOT / 'webapp' / 'e2e' / 'smoke.spec.ts'


def test_ticket_lightweight_frontend_contract_files_exist():
    assert SUPPORT_CONSOLE.exists()
    assert SUPPORT_API.exists()
    assert E2E_SMOKE.exists()


def test_ticket_detail_first_paint_uses_summary_and_timeline_not_heavy_endpoint():
    console = SUPPORT_CONSOLE.read_text(encoding='utf-8')
    api = SUPPORT_API.read_text(encoding='utf-8')

    assert "supportApi.supportConversations" in console
    assert "supportApi.supportConversationDetail" in console
    assert "supportApi.supportConversationState" in console
    assert "supportApi.supportConversationMetrics" in console
    assert "supportApi.supportConversationReply" in console
    assert "supportApi.ticket(" not in console
    assert "/api/support/conversations/detail" in api
    assert "/api/support/conversations/metrics" in api
