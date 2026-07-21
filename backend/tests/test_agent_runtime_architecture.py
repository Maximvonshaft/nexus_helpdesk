from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.agent_runtime import service as agent_service
from app.services.agent_runtime.access_policy import resolve_webchat_agent_access
from app.services.agent_runtime.skill_registry import load_skills, prompt_skill_catalog
from app.services.agent_runtime.tool_adapter import ToolObservation
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
            body="Where is CH020000129135?",
            request_id="request",
            metadata={
                "agent_allowed_tools": ["speedaf.order.query"],
                "agent_execution_context": {
                    "granted_permissions": ["speedaf:tracking:read"]
                },
            },
        ),
        started=0.0,
    )

    assert result.ok is True
    assert result.reply == "The shipment is in transit."
    assert [call.tool_name for call in observed_calls] == ["speedaf.order.query"]
    assert result.tool_calls[0]["tool_name"] == "speedaf.order.query"


class _FailingCommitDb(_Db):
    def __init__(self) -> None:
        self.rolled_back = False

    def commit(self) -> None:
        raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_agent_loop_fails_closed_when_tool_transaction_does_not_commit(monkeypatch) -> None:
    route_calls = 0

    async def route(_self, _request):
        nonlocal route_calls
        route_calls += 1
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            elapsed_ms=3,
            structured_output={
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
            raw_payload_safe_summary={"model": "test"},
        )

    def execute(_db, *, calls, context, allow_high_risk_writes=False):
        del calls, context, allow_high_risk_writes
        return [
            ToolObservation(
                tool_name="speedaf.order.query",
                ok=True,
                status="success",
                result={"status": "in_transit"},
            )
        ]

    db = _FailingCommitDb()
    monkeypatch.setattr(agent_service.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        agent_service,
        "_authoritative_provider_audit_exists",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(agent_service, "execute_agent_tool_calls", execute)

    result = await agent_service._run_agent_with_db(
        db,
        request=RuntimeAIProviderRequest(
            tenant_key="tenant",
            channel_key="website",
            session_id="session",
            body="Where is CH020000129135?",
            request_id="request",
            metadata={
                "agent_allowed_tools": ["speedaf.order.query"],
                "agent_execution_context": {
                    "granted_permissions": ["speedaf:tracking:read"]
                },
            },
        ),
        started=0.0,
    )

    assert route_calls == 1
    assert db.rolled_back is True
    assert result.ai_generated is False
    assert result.error_code == "tool_transaction_commit_failed"
    assert result.tool_calls == [
        {
            "round": 0,
            "tool_name": "speedaf.order.query",
            "status": "failed",
            "ok": False,
            "error_code": "tool_transaction_commit_failed",
        }
    ]


def test_public_agent_access_policy_does_not_derive_grants_from_visible_tools(monkeypatch) -> None:
    monkeypatch.setenv(
        "WEBCHAT_AGENT_ALLOWED_TOOLS",
        "knowledge.search,ticket.create",
    )
    monkeypatch.setenv(
        "WEBCHAT_AGENT_GRANTED_PERMISSIONS",
        "knowledge:read",
    )

    policy = resolve_webchat_agent_access()

    assert policy.allowed_tools == ("knowledge.search",)
    assert policy.granted_permissions == frozenset({"knowledge:read"})
    assert "ticket:create" not in policy.granted_permissions


def test_webchat_agent_callers_use_configured_persona_and_server_access_policy() -> None:
    for relative in (
        "backend/app/services/webchat_ai_service.py",
        "backend/app/services/conversation_ai_service.py",
    ):
        source = Path(relative).read_text(encoding="utf-8")
        assert "resolve_webchat_agent_access" in source
        assert "build_agent_context" in source
        assert "_permissions_for_tools" not in source
        assert '"assistant_name": "Speedy"' not in source
        assert '"brand": "Speedaf"' not in source


def test_canonical_executor_separates_execution_arguments_from_audit_projection() -> None:
    source = Path(
        "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    constructor = source.split("def runtime_tool_actions_from_tool_calls", 1)[1].split(
        "def execute_controlled_tool_calls", 1
    )[0]
    assert "_bounded_execution_arguments(arguments)" in constructor
    assert "_safe_tool_arguments(arguments)" not in constructor
    assert "arguments=_safe_tool_arguments(action.arguments)" in source


def test_provider_capability_contract_has_only_agent_turn() -> None:
    schemas = Path(
        "backend/app/services/provider_runtime/schemas.py"
    ).read_text(encoding="utf-8")
    adapter = Path(
        "backend/app/services/provider_runtime/adapters/private_ai_runtime.py"
    ).read_text(encoding="utf-8")
    assert "agent_turn: bool" in schemas
    assert "webchat_runtime_reply: bool" not in schemas
    assert "agent_turn=True" in adapter
    assert "webchat_runtime_reply=True" not in adapter


def test_canonical_executor_does_not_trust_model_idempotency_keys() -> None:
    source = Path(
        "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    function = source.split("def _idempotency_key_for_action", 1)[1].split(
        "def _safe_tool_arguments", 1
    )[0]
    assert "model_key" not in function
    assert "raw_calls" not in function
    assert "not contract.is_write_tool" in function
    assert '"tenant_id"' in function
    assert '"conversation_id"' in function


def test_canonical_static_authority_runs_agent_runtime_residue_gate() -> None:
    source = Path("scripts/verify_repository.py").read_text(encoding="utf-8")
    assert '"scripts/ci/check_agent_runtime_residue.py"' in source
    assert (
        '_qualification_failures("scripts/ci/check_agent_runtime_residue.py")'
        in source
    )


def test_generic_context_has_no_legacy_domain_compatibility_surface() -> None:
    source = Path(
        "backend/app/services/ai_runtime_context.py"
    ).read_text(encoding="utf-8")
    signature = source.split("def build_agent_context", 1)[1].split(
        ") -> dict[str, Any]:", 1
    )[0]
    assert "**_legacy" not in signature
    assert "tracking_number" not in signature
    assert "tracking_fact_evidence_present" not in signature
    assert "def build_runtime_context_guard(" not in source
    assert not Path("backend/tests/test_runtime_context_guard.py").exists()


def test_provider_accepts_only_the_agent_turn_model_contract() -> None:
    source = Path(
        "backend/app/services/provider_runtime/output_contracts.py"
    ).read_text(encoding="utf-8")
    assert "nexus.agent_turn.v1" in source
    assert "nexus.ai_reply.v3" not in source
    assert "WEBCHAT_RUNTIME_OUTPUT_CONTRACT" not in source


def test_voice_control_plane_is_session_first_and_never_auto_creates_ticket() -> None:
    api = Path("backend/app/api/webchat_voice.py").read_text(encoding="utf-8")
    service = Path("backend/app/services/webchat_voice_service.py").read_text(encoding="utf-8")
    conversation = Path(
        "backend/app/services/conversation_first_service.py"
    ).read_text(encoding="utf-8")
    session_route = '"/admin/voice/{voice_session_id}/accept"'
    legacy_ticket_route = (
        '"/admin/tickets/'
        + '{ticket_id}/voice/{voice_session_id}/accept"'
    )
    assert session_route in api
    assert legacy_ticket_route not in api
    assert "ensure_voice_ticket_for_public_conversation" not in api
    assert "ensure_voice_ticket_for_public_conversation" not in conversation
    assert "_visible_voice_session_context" in service


def test_provider_runtime_has_no_non_authoritative_shadow_execution() -> None:
    traffic = Path(
        "backend/app/services/provider_runtime/traffic_selection.py"
    ).read_text(encoding="utf-8")
    router = Path(
        "backend/app/services/provider_runtime/router.py"
    ).read_text(encoding="utf-8")
    assert "SHADOW_ONLY" not in traffic
    assert '"shadow"' not in traffic.split("_VALID_MODES", 1)[1].split("}", 1)[0]
    assert "provider_shadow_only" not in router
    assert "shadow_candidate_executed" not in router



@pytest.mark.asyncio
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


def test_canonical_executor_has_no_detached_policy_decision_input() -> None:
    source = Path(
        "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    execution = source.split(
        "def execute_controlled_tool_calls", 1
    )[1].split("def _customer_for_context", 1)[0]
    assert "ai_decision" not in execution
    assert (
        "policy_gate_decision = _decision_for_policy_gate(raw_calls, actions)"
        in execution
    )
