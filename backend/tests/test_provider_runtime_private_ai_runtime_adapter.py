from __future__ import annotations

import json

import pytest

from app.services.provider_runtime.adapters.private_ai_runtime import (
    PrivateAIRuntimeAdapter,
    _normalize_agent_turn,
    _parse_json_text,
)
from app.services.provider_runtime.schemas import ProviderRequest


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-agent-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-agent-1",
        "scenario": "agent_turn",
        "body": "Where is my parcel?",
        "recent_context": [{"role": "customer", "text": "hello"}],
        "output_contract": "nexus.agent_turn.v1",
        "timeout_ms": 15000,
        "metadata": {
            "customer_language": "en",
            "persona_context": {"assistant_name": "Speedy", "secret": "must-not-leak"},
            "agent_skills": [{"name": "shipment_tracking", "tools": ["speedaf.order.query"]}],
            "agent_tools": [
                {
                    "name": "speedaf.order.query",
                    "description": "Query current shipment state.",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_observations": [],
        },
    }
    data.update(overrides)
    return ProviderRequest(**data)


def _configure(monkeypatch, tmp_path) -> PrivateAIRuntimeAdapter:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    return PrivateAIRuntimeAdapter()


def test_normalize_agent_turn_accepts_direct_and_wrapped_json() -> None:
    direct = _normalize_agent_turn(
        {
            "customer_reply": "Hello.",
            "intent": "general_support",
            "next_action": "reply",
            "tool_calls": [],
        },
        max_output_chars=4000,
    )
    assert direct["customer_reply"] == "Hello."

    wrapped = _normalize_agent_turn(
        {
            "message": {
                "content": json.dumps(
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
            }
        },
        max_output_chars=4000,
    )
    assert wrapped["next_action"] == "call_tool"


def test_parse_json_text_rejects_missing_json() -> None:
    with pytest.raises(ValueError, match="json_missing"):
        _parse_json_text("plain prose")


def test_prompt_contains_skill_tool_and_observation_contract(monkeypatch, tmp_path) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    request = _request(
        metadata={
            "customer_language": "en",
            "persona_context": {"assistant_name": "Speedy", "token": "must-not-leak"},
            "agent_skills": [{"name": "approved_knowledge", "tools": ["knowledge.search"]}],
            "agent_tools": [{"name": "knowledge.search", "input_schema": {"type": "object"}}],
            "tool_observations": [{"tool_name": "knowledge.search", "ok": True, "result": {"answer": "Approved"}}],
        }
    )

    prompt = adapter._build_prompt(request)

    assert "approved_knowledge" in prompt
    assert "knowledge.search" in prompt
    assert "Approved" in prompt
    assert "must-not-leak" not in prompt
    assert "nexus.agent_turn.v1" in prompt


@pytest.mark.asyncio
async def test_generate_accepts_final_agent_turn(monkeypatch, tmp_path) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, payload, token: {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "Hello, how can I help?",
                        "intent": "general_support",
                        "next_action": "reply",
                        "handoff_required": False,
                        "tool_calls": [],
                    }
                )
            }
        },
    )

    result = await adapter.generate(None, _request())

    assert result.ok is True
    assert result.structured_output["customer_reply"] == "Hello, how can I help?"
    assert result.raw_payload_safe_summary["contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_generate_accepts_tool_request_without_customer_reply(monkeypatch, tmp_path) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, payload, token: {
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
    )

    result = await adapter.generate(None, _request())

    assert result.ok is True
    assert result.structured_output["next_action"] == "call_tool"
    assert result.structured_output["tool_calls"][0]["tool_name"] == "speedaf.order.query"


@pytest.mark.asyncio
async def test_generate_repairs_format_once(monkeypatch, tmp_path) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    responses = [
        {"text": "not json"},
        {
            "customer_reply": "Please provide the missing reference.",
            "intent": "request_information",
            "next_action": "ask_clarifying_question",
            "handoff_required": False,
            "tool_calls": [],
        },
    ]
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, payload, token: responses.pop(0))

    result = await adapter.generate(None, _request())

    assert result.ok is True
    assert result.structured_output["next_action"] == "ask_clarifying_question"
    assert result.raw_payload_safe_summary["contract_repair_applied"] is True
    assert responses == []


def test_production_requires_token_file(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_TOKEN", raising=False)

    adapter = PrivateAIRuntimeAdapter()

    assert adapter._config_error() == "private_ai_runtime_token_file_required"
