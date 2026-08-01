from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "webapp" / "e2e" / "rc-live.spec.ts"


def test_rc_browser_proves_one_conversation_human_reply_and_closure() -> None:
    source = SPEC.read_text(encoding="utf-8")

    required_markers = (
        "const publicPage = await context.newPage()",
        "const operatorSessionKey = `webchat:${conversationId}`",
        "markStage('operator-handoff-requested')",
        "getByRole('button', { name: '接受会话', exact: true })",
        "getByRole('combobox', { name: '客服状态' })",
        "getByRole('option', { name: '在线', exact: true })",
        "url.pathname === '/api/operator/agent-state'",
        "markStage('operator-handoff-assigned')",
        "const replyField = page.getByLabel('回复客户')",
        "url.pathname === `/api/operator/conversations/${conversationId}/reply`",
        "getByRole('button', { name: '发送回复', exact: true })",
        "publicPage.locator('.nd-webchat-msg', { hasText: operatorReply })",
        "markStage('operator-close')",
        "getByLabel('会话结果')",
        "getByRole('option', { name: '人工在线解决', exact: true })",
        "getByRole('button', { name: '确认结束并释放名额', exact: true })",
        "url.pathname === `/api/operator/conversations/${conversationId}/close`",
        "status: 'closed'",
        "outcome: 'human_resolved'",
        "markStage('capacity-released')",
        "active_agent_id: null",
    )
    for marker in required_markers:
        assert marker in source

    first_send = source.index("markStage('public-send-first')")
    second_send = source.index("markStage('public-send-second')")
    handoff = source.index("markStage('operator-handoff-requested')")
    reply = source.index("markStage('operator-reply')")
    customer_receipt = source.index("markStage('customer-received')")
    close = source.index("markStage('operator-close')")
    released = source.index("markStage('capacity-released')")
    completed = source.index("markStage('completed')")

    assert first_send < second_send < handoff < reply < customer_receipt < close < released < completed


def test_rc_browser_uses_product_authorities_without_test_only_backdoors() -> None:
    source = SPEC.read_text(encoding="utf-8")

    assert "/api/operator/agent-state" in source
    assert "/api/operator/conversations/${conversationId}/reply" in source
    assert "/api/operator/conversations/${conversationId}/close" in source
    assert "/api/support/conversations?view=all&channel=webchat&limit=100" in source

    for forbidden in (
        "seed_rc_test_data",
        "SessionLocal",
        "WebchatConversation(",
        "WebchatHandoffRequest(",
        "active_agent_id =",
        "handoff_status =",
        "page.route(",
    ):
        assert forbidden not in source
