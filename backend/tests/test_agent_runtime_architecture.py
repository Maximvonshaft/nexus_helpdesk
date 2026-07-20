from __future__ import annotations

import json

import pytest

from app.services.agent_runtime import service as agent_service
from app.services.agent_runtime.skill_registry import load_skills, prompt_skill_catalog
from app.services.agent_runtime.tool_executor import ToolObservation
from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
from app.services.provider_runtime.output_contracts import OutputContracts
from app.services.provider_runtime.schemas import ProviderResult
from app.services.webchat_ai_decision_runtime.schemas import AIDecision
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract, registered_tool_names


def test_skill_registry_references_only_canonical_tools() -> None:
    skills = load_skills()
    assert skills
    assert len({skill.name for skill in skills}) == len(skills)
    for skill in skills:
        assert skill.instructions
        assert all(get_tool_contract(name) is not None for name in skill.tools)
    projected = prompt_skill_catalog(available_tools=set(registered_tool_names()))
    assert {item["name"] for item in projected} == {skill.name for skill in skills}


def test_agent_turn_contract_distinguishes_tool_and_final_turns() -> None:
    tool_turn = AIDecision.model_validate(
        {
            "customer_reply": None,
            "intent": "shipment_tracking",
            "next_action": "call_tool",
            "tool_calls": [
                {
                    "tool_name": "speedaf.order.query",
                    "arguments": {"tracking_number": "CH020000129135"},
                }
            ],
        }
    )
    assert tool_turn.next_action == "call_tool"
    assert tool_turn.customer_reply is None

    final_turn = AIDecision.model_validate(
        {
            "customer_reply": "The Tool could not verify the shipment right now.",
            "intent": "shipment_tracking",
            "next_action": "reply",
            "tool_calls": [],
        }
    )
    assert final_turn.customer_reply

    with pytest.raises(ValueError):
        AIDecision.model_validate(
            {
                "customer_reply": "I already answered.",
                "next_action": "call_tool",
                "tool_calls": [{"tool_name": "knowledge.search", "arguments": {"query": "x"}}],
            }
        )


def test_output_contract_does_not_infer_business_truth_from_words() -> None:
    parsed = OutputContracts.validate_and_parse(
        "nexus.agent_turn.v1",
        '{"customer_reply":"您的包裹正在运输中。","intent":"shipment_tracking","next_action":"reply","tool_calls":[],"handoff_required":false}',
    )
    assert parsed["customer_reply"] == "您的包裹正在运输中。"

    credential_text = ("Bear" + "er ") + ("a" * 26)
    with pytest.raises(ValueError, match="secret|credential|Potential"):
        OutputContracts.validate_and_parse(
            "nexus.agent_turn.v1",
            json.dumps(
                {
                    "customer_reply": credential_text,
                    "intent": "support",
                    "next_action": "reply",
                    "tool_calls": [],
                    "handoff_required": False,
                }
            ),
        )


class _Db:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_then_returns_final_reply(monkeypatch) -> None:
    outputs = [
        {
            "customer_reply": None,
            "intent": "shipment_tracking",
            "next_action": "call_tool",
            "handoff_required": False,
            "tool_calls": [
                {
                    "tool_name": "speedaf.order.query",
                    "arguments": {"tracking_number": "CH020000129135"},
                }
            ],
        },
        {
            "customer_reply": "The shipment is in transit.",
            "intent": "shipment_tracking",
            "next_action": "reply",
            "handoff_required": False,
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

    observed_calls = []

    def execute(_db, *, calls, context, allow_high_risk_writes=False):
        del context, allow_high_risk_writes
        observed_calls.extend(calls)
        return [
            ToolObservation(
                tool_name="speedaf.order.query",
                ok=True,
                status="success",
                result={"status": "in_transit"},
            )
        ]

    monkeypatch.setattr(agent_service.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(agent_service, "execute_agent_tool_calls", execute)
    result = await agent_service._run_agent_with_db(
        _Db(),
        request=RuntimeAIProviderRequest(
            tenant_key="tenant",
            channel_key="website",
            session_id="session",
            body="Where is CH020000129135?",
            request_id="request",
            metadata={"agent_allowed_tools": ["speedaf.order.query"]},
        ),
        started=0.0,
    )

    assert result.ok is True
    assert result.reply == "The shipment is in transit."
    assert [call.tool_name for call in observed_calls] == ["speedaf.order.query"]
    assert result.tool_calls[0]["tool_name"] == "speedaf.order.query"
