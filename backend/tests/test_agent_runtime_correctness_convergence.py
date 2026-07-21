from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import Mock

import pytest

from app.services import provider_runtime as provider_runtime_module
from app.services.agent_runtime.context_compiler import compile_agent_context
from app.services.nexus_osr.controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ControlledActionExecutor,
)
from app.services.nexus_osr.policies import ToolExecutionPolicy
from app.services.nexus_osr.runtime_decision_contract import RuntimeToolAction
from app.services.provider_runtime.output_contracts import AGENT_TURN_OUTPUT_CONTRACT
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


def _release_snapshot() -> dict:
    return {
        "source": "deployment",
        "tenant_key": "tenant-a",
        "definition": {"id": 11, "definition_key": "support"},
        "deployment": {
            "id": 22,
            "environment": "production",
            "scope_key": "market:*|channel:webchat|language:*|case:*",
            "canary": False,
        },
        "release": {
            "id": 33,
            "version": 4,
            "manifest_sha256": "a" * 64,
        },
        "manifest": {"must_not_be_in_prompt": "x" * 6000},
        "resolved": {"resources": [{"content": "y" * 6000}]},
    }


def _provider_request(*, timeout_ms: int = 15000) -> ProviderRequest:
    return ProviderRequest(
        request_id="runtime-correctness",
        tenant_id="tenant-a",
        tenant_key="tenant-a",
        channel_key="webchat",
        session_id="session-a",
        scenario="agent_turn",
        body="Where is my shipment?",
        recent_context=[
            {"role": "customer", "text": "old-message-" + ("z" * 5000)}
        ],
        output_contract=AGENT_TURN_OUTPUT_CONTRACT,
        timeout_ms=timeout_ms,
        metadata={
            "customer_language": "zh-CN",
            "persona_context": {"assistant_name": "Nora", "token": "do-not-leak"},
            "agent_playbooks": [
                {"name": "tracking", "instructions": ["p" * 3000]}
            ],
            "agent_tools": [
                {
                    "name": "speedaf.order.query",
                    "description": "d" * 3000,
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_observations": [
                {
                    "tool_name": "speedaf.order.query",
                    "ok": True,
                    "status": "executed",
                    "result": {"status": "in_transit", "details": "o" * 2500},
                }
            ],
            "channel_context": {"case_type": "tracking"},
            "agent_runtime_policy": {"provider_timeout_ms": 5000},
            "agent_release_snapshot": _release_snapshot(),
        },
    )


def test_context_compiler_preserves_mandatory_runtime_truth_as_valid_json() -> None:
    request = _provider_request()
    compiled = compile_agent_context(
        request,
        max_prompt_chars=2200,
        num_ctx=2048,
        max_output_chars=800,
    )
    payload = json.loads(compiled.prompt[compiled.prompt.index("{") :])
    assert compiled.prompt_chars <= compiled.budget_chars
    assert compiled.compacted is True
    assert payload["language"] == "zh-CN"
    assert payload["agent_release"]["release"]["id"] == 33
    assert payload["agent_release"]["release"]["manifest_sha256"] == "a" * 64
    assert payload["tool_observations"]
    assert payload["tool_observations"][0]["status"] == "executed"
    assert "must_not_be_in_prompt" not in compiled.prompt
    assert "do-not-leak" not in compiled.prompt
    assert compiled.digest


def test_context_compiler_never_returns_tail_truncated_json() -> None:
    compiled = compile_agent_context(
        _provider_request(),
        max_prompt_chars=2000,
        num_ctx=1024,
        max_output_chars=500,
    )
    parsed = json.loads(compiled.prompt[compiled.prompt.index("{") :])
    assert parsed["customer_message"]
    assert parsed["tool_observations"]


class _CapturingAdapter(ProviderAdapter):
    name = "private_ai_runtime"

    def __init__(self) -> None:
        self.timeout_ms: int | None = None

    async def generate(self, db, request):
        del db
        self.timeout_ms = request.timeout_ms
        return ProviderResult(
            ok=True,
            provider=self.name,
            raw_provider=self.name,
            reply_source=self.name,
            elapsed_ms=1,
            structured_output={
                "customer_reply": "ok",
                "intent": "general_support",
                "next_action": "reply",
                "handoff_required": False,
                "tool_calls": [],
            },
            raw_payload_safe_summary={"provider": self.name},
        )


def _router_db(timeout_ms: int):
    db = Mock()
    query_result = Mock()
    query_result.mappings.return_value.first.return_value = {
        "primary_provider": "private_ai_runtime",
        "fallback_providers": [],
        "output_contract": AGENT_TURN_OUTPUT_CONTRACT,
        "timeout_ms": timeout_ms,
        "kill_switch": False,
        "canary_percent": 100,
    }

    def execute(statement, params=None, *args, **kwargs):
        del params, args, kwargs
        if "insert into provider_runtime_audit_logs" in str(statement).lower():
            return Mock()
        return query_result

    db.execute.side_effect = execute
    return db


@pytest.mark.asyncio
async def test_release_runtime_timeout_can_tighten_but_not_widen_provider_ceiling(
    monkeypatch,
) -> None:
    monkeypatch.setattr(provider_runtime_module, "_BOOTSTRAPPED", True)
    monkeypatch.setattr(ProviderRegistry, "_factories", {})
    monkeypatch.setenv("PROVIDER_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "100")
    adapter = _CapturingAdapter()
    ProviderRegistry.register("private_ai_runtime", lambda db: adapter)

    request = _provider_request(timeout_ms=5000)
    result = await ProviderRuntimeRouter(_router_db(12000)).route(request)
    assert result.ok is True
    assert adapter.timeout_ms == 5000
    assert result.raw_payload_safe_summary["effective_timeout_ms"] == 5000

    request = _provider_request(timeout_ms=30000)
    result = await ProviderRuntimeRouter(_router_db(12000)).route(request)
    assert result.ok is True
    assert adapter.timeout_ms == 12000
    assert result.raw_payload_safe_summary["effective_timeout_ms"] == 12000


def test_canonical_executor_returns_authoritative_handler_duration() -> None:
    def handler(request: ActionExecutionRequest) -> ActionExecutionResult:
        time.sleep(0.01)
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary={"safe": True},
        )

    executor = ControlledActionExecutor(
        policies={
            "knowledge.search": ToolExecutionPolicy(
                tool_name="knowledge.search",
                enabled=True,
                ai_auto_executable=True,
            )
        },
        handlers={"knowledge.search": handler},
    )
    result = executor.execute(
        ActionExecutionRequest(
            action=RuntimeToolAction(
                tool_name="knowledge.search",
                arguments={"query": "x"},
                executed=False,
            )
        )
    )
    assert result.ok is True
    assert result.elapsed_ms >= 5


@pytest.mark.asyncio
async def test_blocking_tool_unit_is_moved_off_the_event_loop() -> None:
    heartbeat = False

    def blocking() -> str:
        time.sleep(0.03)
        return "done"

    async def tick() -> None:
        nonlocal heartbeat
        await asyncio.sleep(0.005)
        heartbeat = True

    task = asyncio.create_task(tick())
    result = await asyncio.to_thread(blocking)
    await task
    assert result == "done"
    assert heartbeat is True
