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
    assert authority.exists()
    assert not (ROOT / "backend/app/services/agent_runtime/fallback.py").exists()

    for relative in (
        "backend/app/services/agent_runtime/service.py",
        "backend/app/services/webchat_runtime_ai_service.py",
        "backend/app/services/webchat_ai_service.py",
        "backend/app/services/conversation_ai_service.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "customer_visible_fallback" in source
        assert "customer_visible_runtime_fallback" not in source
        assert "def _localized_fallback(" not in source

    wrapper = (
        ROOT / "backend/app/services/webchat_runtime_ai_service.py"
    ).read_text(encoding="utf-8")
    assert "def _fallback(" not in wrapper


def test_ticketless_runtime_cannot_end_with_blank_agent_output() -> None:
    source = (
        ROOT / "backend/app/services/conversation_ai_service.py"
    ).read_text(encoding="utf-8")
    terminal_section = source.split(
        "safe_runtime_trace = sanitized_ai_turn_runtime_trace(",
        1,
    )[1].split("message = WebchatMessage(", 1)[0]

    assert '"status": "failed_no_public_reply"' not in terminal_section
    assert "customer_visible_fallback" in terminal_section
    assert "customer_visible_policy_blocked" in terminal_section
    assert "handoff_tool_side_effect_missing" in terminal_section


def test_handoff_terminal_truth_comes_from_committed_observation() -> None:
    source = (
        ROOT / "backend/app/services/agent_runtime/service.py"
    ).read_text(encoding="utf-8")
    final_turn = source.split(
        'if decision.next_action != "call_tool":',
        1,
    )[1].split("if round_index >= max_rounds:", 1)[0]

    assert "handoff_committed = _committed_handoff_observed(state)" in final_turn
    assert "handoff_required=handoff_committed" in final_turn
    assert "handoff_required=decision.handoff_required" not in final_turn
    assert "handoff_tool_side_effect_missing" in final_turn
