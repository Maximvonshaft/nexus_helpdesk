from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.agent_control_config import validate_agent_config_content
from app.services.agent_runtime import runtime as agent_runtime
from app.services.agent_runtime.access_policy import resolve_webchat_agent_access
from app.services.agent_runtime.tool_adapter import ToolObservation
from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
from app.services.provider_runtime.output_contracts import OutputContracts
from app.services.provider_runtime.schemas import ProviderResult
from app.services.webchat_ai_decision_runtime.schemas import AIDecision
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract

ROOT = Path(__file__).resolve().parents[2]


def test_playbook_contract_references_only_canonical_tools() -> None:
    playbook = validate_agent_config_content(
        "playbook",
        {
            "name": "shipment_tracking",
            "display_name": "Shipment tracking",
            "description": "Query shipment facts.",
            "tools": ["speedaf.order.query", "knowledge.search"],
            "instructions": ["Use Tool observations as the source of truth."],
            "enabled": True,
        },
    )
    assert playbook["schema_version"] == "nexus.agent_playbook.v1"
    assert all(get_tool_contract(name) is not None for name in playbook["tools"])
    with pytest.raises(HTTPException):
        validate_agent_config_content(
            "playbook",
            {
                "name": "bad",
                "description": "Bad Playbook.",
                "tools": ["parallel.executor"],
                "instructions": ["Do not exist."],
            },
        )


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
                "tool_calls": [
                    {
                        "tool_name": "knowledge.search",
                        "arguments": {"query": "x"},
                    }
                ],
            }
        )


def test_output_contract_does_not_infer_business_truth_from_words() -> None:
    parsed = OutputContracts.validate_and_parse(
        "nexus.agent_turn.v1",
        json.dumps(
            {
                "customer_reply": "您的包裹正在运输中。",
                "intent": "shipment_tracking",
                "next_action": "reply",
                "tool_calls": [],
                "handoff_required": False,
            },
            ensure_ascii=False,
        ),
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


def _bind_release_runtime(monkeypatch) -> None:
    snapshot = {
        "schema_version": "nexus.agent_release.v1",
        "source": "deployment",
        "tenant_key": "tenant",
        "release": {"id": 1, "version": 1, "manifest_sha256": "a" * 64},
        "deployment": {"id": 1, "environment": "production"},
        "manifest": {"integrations": [], "knowledge": []},
        "resolved": {"allowed_tools": ["speedaf.order.query"]},
    }
    resolved = SimpleNamespace(
        snapshot=snapshot,
        digest="b" * 64,
        deployment=SimpleNamespace(id=1),
        release=SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        agent_runtime,
        "resolve_agent_release",
        lambda *_args, **_kwargs: resolved,
    )
    monkeypatch.setattr(
        agent_runtime,
        "record_run_snapshot",
        lambda *_args, **_kwargs: None,
    )
    run = SimpleNamespace(
        id=1,
        trace_id="trace-1",
        tenant_key="tenant",
        session_id="session",
        request_id="request",
        release_id=1,
        status="running",
        final_action=None,
        error_code=None,
    )
    monkeypatch.setattr(
        agent_runtime,
        "start_agent_run",
        lambda *_args, **_kwargs: run,
    )
    monkeypatch.setattr(
        agent_runtime,
        "bind_agent_run_release",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_runtime,
        "append_agent_event",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_runtime,
        "finish_agent_run",
        lambda *_args, **_kwargs: run,
    )
    monkeypatch.setattr(
        agent_runtime,
        "_runtime_policy",
        lambda *_args, **_kwargs: {
            "max_tool_rounds": 3,
            "allow_high_risk_writes": False,
            "allowed_tools": [],
            "provider_timeout_ms": 15000,
            "enabled": True,
        },
    )
    monkeypatch.setattr(
        agent_runtime,
        "prompt_playbook_catalog",
        lambda *_args, **_kwargs: [
            {
                "resource_key": "agent.playbook.shipment-tracking",
                "name": "shipment_tracking",
                "tools": ["speedaf.order.query"],
                "instructions": ["Use the Tool observation."],
            }
        ],
    )


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
                status="executed",
                result={"status": "in_transit"},
            )
        ]

    _bind_release_runtime(monkeypatch)
    monkeypatch.setattr(agent_runtime.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        agent_runtime,
        "_authoritative_provider_audit_exists",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(agent_runtime, "execute_agent_tool_calls", execute)
    result = await agent_runtime.run_agent_with_db(
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
        started=time.monotonic(),
    )
    assert result.ok is True
    assert result.reply == "The shipment is in transit."
    assert [call.tool_name for call in observed_calls] == ["speedaf.order.query"]
    assert result.tool_calls[0]["tool_name"] == "speedaf.order.query"


def test_canonical_runtime_leaves_tool_transaction_commit_to_worker() -> None:
    source = (
        ROOT / "backend/app/services/agent_runtime/runtime.py"
    ).read_text(encoding="utf-8")
    assert "tool_transaction_commit_failed" not in source
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"run_agent", "run_agent_with_db"}
    }
    assert set(functions) == {"run_agent", "run_agent_with_db"}
    for function in functions.values():
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
            for node in ast.walk(function)
        )


def test_public_agent_access_policy_does_not_derive_grants_from_visible_tools(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "WEBCHAT_AGENT_ALLOWED_TOOLS",
        "knowledge.search,ticket.create",
    )
    monkeypatch.setenv("WEBCHAT_AGENT_GRANTED_PERMISSIONS", "knowledge:read")
    policy = resolve_webchat_agent_access()
    assert policy.allowed_tools == ("knowledge.search",)
    assert policy.granted_permissions == frozenset({"knowledge:read"})
    assert "ticket:create" not in policy.granted_permissions


def test_canonical_agent_runtime_has_no_static_skill_chain() -> None:
    assert (ROOT / "backend/app/services/agent_runtime/runtime.py").exists()
    assert (ROOT / "backend/app/services/agent_runtime/playbook_registry.py").exists()
    assert not (ROOT / "backend/app/services/agent_runtime/service.py").exists()
    assert not (ROOT / "backend/app/services/agent_runtime/skill_registry.py").exists()
    assert not (ROOT / "backend/app/agent_skills/skills.json").exists()
    runtime = (
        ROOT / "backend/app/services/agent_runtime/runtime.py"
    ).read_text(encoding="utf-8")
    assert "agent_playbooks" in runtime
    assert "agent_skills" not in runtime
    assert "prompt_playbook_catalog" in runtime


def test_webchat_uses_one_configured_agent_reply_authority() -> None:
    path = ROOT / "backend/app/services/webchat_ai_service.py"
    source = path.read_text(encoding="utf-8")
    assert path.exists()
    assert not (ROOT / "backend/app/services/conversation_ai_service.py").exists()
    assert "resolve_webchat_agent_access" in source
    assert "build_agent_context" in source
    assert "ticket_id: int | None" in source
    assert "_persist_ticket_reply(" in source
    assert "_persist_ticketless_reply(" in source
    assert "_permissions_for_tools" not in source
    assert '"assistant_name": "Speedy"' not in source
    assert '"brand": "Speedaf"' not in source


def test_canonical_executor_separates_execution_arguments_from_audit_projection() -> None:
    source = (
        ROOT / "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    constructor = source.split(
        "def runtime_tool_actions_from_tool_calls", 1
    )[1].split("def execute_controlled_tool_calls", 1)[0]
    assert "_bounded_execution_arguments(arguments)" in constructor
    assert "_safe_tool_arguments(arguments)" not in constructor
    assert "arguments=_safe_tool_arguments(action.arguments)" in source


def test_provider_capability_contract_has_only_agent_turn() -> None:
    schemas = (
        ROOT / "backend/app/services/provider_runtime/schemas.py"
    ).read_text(encoding="utf-8")
    adapter = (
        ROOT
        / "backend/app/services/provider_runtime/adapters/private_ai_runtime.py"
    ).read_text(encoding="utf-8")
    assert "agent_turn: bool" in schemas
    assert "webchat_runtime_reply: bool" not in schemas
    assert "agent_turn=True" in adapter
    assert "webchat_runtime_reply=True" not in adapter


def test_canonical_executor_does_not_trust_model_idempotency_keys() -> None:
    source = (
        ROOT / "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    function = source.split("def _idempotency_key_for_action", 1)[1].split(
        "def _safe_tool_arguments", 1
    )[0]
    assert "model_key" not in function
    assert "raw_calls" not in function
    assert "not contract.is_write_tool" in function
    assert '"tenant_id"' in function
    assert '"conversation_id"' in function


def test_generic_context_has_no_legacy_domain_compatibility_surface() -> None:
    source = (
        ROOT / "backend/app/services/ai_runtime_context.py"
    ).read_text(encoding="utf-8")
    signature = source.split("def build_agent_context", 1)[1].split(
        ") -> dict[str, Any]:", 1
    )[0]
    assert "**_legacy" not in signature
    assert "tracking_number" not in signature
    assert "tracking_fact_evidence_present" not in signature
    assert "def build_runtime_context_guard(" not in source
    assert not (ROOT / "backend/tests/test_runtime_context_guard.py").exists()


def test_provider_accepts_only_the_agent_turn_model_contract() -> None:
    source = (
        ROOT / "backend/app/services/provider_runtime/output_contracts.py"
    ).read_text(encoding="utf-8")
    assert "nexus.agent_turn.v1" in source
    assert "nexus.ai_reply.v3" not in source
    assert "WEBCHAT_RUNTIME_OUTPUT_CONTRACT" not in source
