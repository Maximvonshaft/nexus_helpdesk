#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "backend/app/services/agent_runtime/service.py"
TESTS = ROOT / "backend/tests/test_agent_runtime_architecture.py"
RESIDUE = ROOT / "scripts/ci/check_agent_runtime_residue.py"
ARCH = ROOT / "docs/architecture/generic-agent-skill-runtime.md"
WORKFLOW = ROOT / ".github/workflows/temporary-committed-handoff-patch.yml"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_service() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    old = '''        if decision.next_action != "call_tool":
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action=decision.next_action,
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                )
            )
            return RuntimeAIProviderResult(
                ok=True,
                ai_generated=True,
                reply_source=result.provider,
                raw_provider=result.raw_provider or result.provider,
                raw_payload_safe_summary=_safe_summary(
                    state,
                    decision=decision,
                    provider_summary=result.raw_payload_safe_summary,
                ),
                reply=decision.customer_reply,
                intent=decision.intent,
                handoff_required=decision.handoff_required,
                handoff_reason=decision.handoff_reason,
                recommended_agent_action=(
                    "Review the conversation and take over."
                    if decision.handoff_required
                    else None
                ),
                tool_calls=list(state.executed_calls),
                elapsed_ms=state.elapsed_ms,
                error_code=None,
                retry_after_ms=None,
            )
'''
    new = '''        if decision.next_action != "call_tool":
            handoff_committed = _committed_handoff_observed(state)
            handoff_requested = (
                decision.next_action == "request_handoff"
                or decision.handoff_required
            )
            if handoff_requested and not handoff_committed:
                state.traces.append(
                    AgentRoundTrace(
                        round_index=round_index,
                        next_action=decision.next_action,
                        provider=result.provider,
                        elapsed_ms=result.elapsed_ms,
                        error_code="handoff_tool_side_effect_missing",
                    )
                )
                return _fallback_result(
                    request,
                    state=state,
                    error_code="handoff_tool_side_effect_missing",
                    elapsed_ms=state.elapsed_ms,
                )
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action=decision.next_action,
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                )
            )
            return RuntimeAIProviderResult(
                ok=True,
                ai_generated=True,
                reply_source=result.provider,
                raw_provider=result.raw_provider or result.provider,
                raw_payload_safe_summary=_safe_summary(
                    state,
                    decision=decision,
                    provider_summary=result.raw_payload_safe_summary,
                ),
                reply=decision.customer_reply,
                intent=decision.intent,
                handoff_required=handoff_committed,
                handoff_reason=(
                    decision.handoff_reason or "handoff_requested"
                    if handoff_committed
                    else None
                ),
                recommended_agent_action=(
                    "Review the conversation and take over."
                    if handoff_committed
                    else None
                ),
                tool_calls=list(state.executed_calls),
                elapsed_ms=state.elapsed_ms,
                error_code=None,
                retry_after_ms=None,
            )
'''
    if old in text:
        text = replace_once(text, old, new, label="final handoff authority")
    elif new not in text:
        raise RuntimeError("final handoff authority: expected old or new block")

    helper = '''\n\ndef _committed_handoff_observed(state: AgentRunState) -> bool:
    """Derive handoff truth only from a committed canonical Tool Observation."""

    return any(
        observation.tool_name == "handoff.request.create"
        and observation.ok
        and observation.status in {"executed", "duplicate"}
        for observation in state.observations
    )
'''
    anchor = "\ndef _failed_tool_observations(\n"
    if "def _committed_handoff_observed(" not in text:
        text = replace_once(text, anchor, helper + anchor, label="handoff helper anchor")
    SERVICE.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    if "test_model_only_handoff_final_fails_closed" not in text:
        text += '''\n\n\n@pytest.mark.asyncio
async def test_model_only_handoff_final_fails_closed(monkeypatch) -> None:
    async def route(_self, _request):
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            elapsed_ms=3,
            structured_output={
                "customer_reply": "I will connect you to a human.",
                "intent": "human_handoff",
                "next_action": "request_handoff",
                "handoff_required": True,
                "handoff_reason": "customer_requested_human",
                "tool_calls": [],
            },
            raw_payload_safe_summary={"model": "test"},
        )

    monkeypatch.setattr(agent_service.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        agent_service,
        "_authoritative_provider_audit_exists",
        lambda *_args, **_kwargs: True,
    )
    result = await agent_service._run_agent_with_db(
        _Db(),
        request=RuntimeAIProviderRequest(
            tenant_key="tenant",
            channel_key="website",
            session_id="session",
            body="I need a human.",
            request_id="request-handoff-without-tool",
            metadata={},
        ),
        started=0.0,
    )

    assert result.ai_generated is False
    assert result.handoff_required is False
    assert result.error_code == "handoff_tool_side_effect_missing"
    assert result.reply


@pytest.mark.asyncio
async def test_committed_handoff_observation_is_terminal_authority(monkeypatch) -> None:
    outputs = [
        {
            "customer_reply": None,
            "intent": "human_handoff",
            "next_action": "call_tool",
            "handoff_required": False,
            "tool_calls": [
                {
                    "tool_name": "handoff.request.create",
                    "arguments": {"reason": "customer_requested_human"},
                }
            ],
        },
        {
            "customer_reply": "A human support handoff has been requested.",
            "intent": "human_handoff",
            "next_action": "request_handoff",
            "handoff_required": True,
            "handoff_reason": "customer_requested_human",
            "tool_calls": [],
        },
    ]

    async def route(_self, _request):
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            elapsed_ms=3,
            structured_output=outputs.pop(0),
            raw_payload_safe_summary={"model": "test"},
        )

    def execute(_db, *, calls, context, allow_high_risk_writes=False):
        del calls, context, allow_high_risk_writes
        return [
            ToolObservation(
                tool_name="handoff.request.create",
                ok=True,
                status="executed",
                result={"handoff_request_id": 42},
            )
        ]

    monkeypatch.setattr(agent_service.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        agent_service,
        "_authoritative_provider_audit_exists",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(agent_service, "execute_agent_tool_calls", execute)
    result = await agent_service._run_agent_with_db(
        _Db(),
        request=RuntimeAIProviderRequest(
            tenant_key="tenant",
            channel_key="website",
            session_id="session",
            body="I need a human.",
            request_id="request-handoff-with-tool",
            metadata={
                "agent_allowed_tools": ["handoff.request.create"],
                "agent_execution_context": {
                    "granted_permissions": ["webchat:handoff:create"]
                },
            },
        ),
        started=0.0,
    )

    assert result.ok is True
    assert result.ai_generated is True
    assert result.handoff_required is True
    assert result.error_code is None
'''
    TESTS.write_text(text, encoding="utf-8")


def patch_residue() -> None:
    text = RESIDUE.read_text(encoding="utf-8")
    marker = '    "handoff_required=decision.handoff_required",\n'
    if marker not in text:
        anchor = '    \'"brand": "Speedaf"\',\n'
        text = replace_once(text, anchor, anchor + marker, label="handoff residue anchor")
    RESIDUE.write_text(text, encoding="utf-8")


def patch_architecture() -> None:
    text = ARCH.read_text(encoding="utf-8")
    sentence = (
        "A final handoff claim is authoritative only when a committed "
        "`handoff.request.create` Tool Observation exists; model-only handoff "
        "flags fail closed to the terminal fallback."
    )
    if sentence not in text:
        text = text.rstrip() + "\n\n" + sentence + "\n"
    ARCH.write_text(text, encoding="utf-8")


def cleanup() -> None:
    WORKFLOW.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)


def main() -> None:
    patch_service()
    patch_tests()
    patch_residue()
    patch_architecture()
    cleanup()


if __name__ == "__main__":
    main()
