from __future__ import annotations

from pathlib import Path

from app.services.agent_runtime.terminal_reply import customer_visible_fallback

ROOT = Path(__file__).resolve().parents[2]


def test_terminal_reply_localization_is_deterministic() -> None:
    assert customer_visible_fallback("zh-CN", "hello").startswith("抱歉")
    assert customer_visible_fallback("de-DE", "hello").startswith("Entschuldigung")
    assert customer_visible_fallback("en", "hello").startswith("Sorry")
    assert customer_visible_fallback(None, "中文请求").startswith("抱歉")


def test_terminal_reply_has_one_physical_authority() -> None:
    authority = ROOT / "backend/app/services/agent_runtime/terminal_reply.py"
    runtime = ROOT / "backend/app/services/agent_runtime/runtime.py"
    unified_reply = ROOT / "backend/app/services/webchat_ai_service.py"
    assert authority.exists()
    assert runtime.exists()
    assert unified_reply.exists()
    for retired in (
        "backend/app/services/agent_runtime/fallback.py",
        "backend/app/services/agent_runtime/service.py",
        "backend/app/services/conversation_ai_service.py",
        "backend/app/services/llm_service.py",
        "backend/app/services/auto_reply_service.py",
    ):
        assert not (ROOT / retired).exists()

    for relative in (
        "backend/app/services/agent_runtime/runtime.py",
        "backend/app/services/webchat_runtime_ai_service.py",
        "backend/app/services/webchat_ai_service.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "customer_visible_fallback" in source
        assert "customer_visible_runtime_fallback" not in source
        assert "def _localized_fallback(" not in source

    wrapper = (
        ROOT / "backend/app/services/webchat_runtime_ai_service.py"
    ).read_text(encoding="utf-8")
    assert "def _fallback(" not in wrapper


def test_ticketed_and_ticketless_runtime_share_terminal_decision() -> None:
    source = (
        ROOT / "backend/app/services/webchat_ai_service.py"
    ).read_text(encoding="utf-8")
    function = source.split("def process_webchat_ai_reply_job(", 1)[1].split(
        "def _public_reply_decision(", 1
    )[0]
    assert "ticket_id: int | None" in function
    assert "_public_reply_decision(" in function
    assert "_persist_ticket_reply(" in function
    assert "_persist_ticketless_reply(" in function
    assert "process_ticketless_ai_reply" not in source
    assert "customer_visible_policy_blocked" in source
    assert "handoff_tool_side_effect_missing" in source


def test_handoff_terminal_truth_comes_from_committed_observation() -> None:
    source = (
        ROOT / "backend/app/services/agent_runtime/runtime.py"
    ).read_text(encoding="utf-8")
    terminal_helper = source.split("def _terminal_decision(", 1)[1].split(
        "def _terminal_fallback(", 1
    )[0]
    assert "handoff_committed = _committed_handoff_observed(state)" in terminal_helper
    assert "handoff_required=handoff_committed" in terminal_helper
    assert "handoff_required=decision.handoff_required" not in terminal_helper
    assert "handoff_tool_side_effect_missing" in terminal_helper
