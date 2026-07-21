from __future__ import annotations

import json

import pytest

from app.services.provider_runtime.adapters import private_ai_runtime as private_runtime
from app.services.provider_runtime.schemas import ProviderRequest


def _model_content() -> dict:
    return {
        "schema_version": "nexus.agent_model_profile.v1",
        "provider": "private_ai_runtime",
        "endpoint_url": "http://ai-runtime.internal:18081",
        "credential_ref": None,
        "request_path": "/api/chat",
        "request_shape": "ollama_chat",
        "model": "qwen2.5:3b",
        "temperature": 0.1,
        "top_p": 0.85,
        "max_prompt_chars": 12000,
        "max_output_chars": 4000,
        "num_predict": 512,
        "num_ctx": 8192,
        "keep_alive": "24h",
        "timeout_seconds": 12,
        "enabled": True,
    }


def _release_snapshot() -> dict:
    return {
        "source": "deployment",
        "release": {"id": 7, "version": 3},
        "resolved": {
            "resources": [
                {
                    "id": 9,
                    "resource_key": "agent.model.private-test",
                    "config_type": "model_profile",
                    "version": 2,
                    "content": _model_content(),
                }
            ]
        },
    }


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
            "persona_context": {
                "assistant_name": "Nora",
                "secret": "must-not-leak",
            },
            "agent_playbooks": [
                {
                    "name": "shipment_tracking",
                    "tools": ["speedaf.order.query"],
                }
            ],
            "agent_tools": [
                {
                    "name": "speedaf.order.query",
                    "description": "Query current shipment state.",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_observations": [],
            "agent_release_snapshot": _release_snapshot(),
        },
    }
    data.update(overrides)
    return ProviderRequest(**data)


def _configure(monkeypatch, tmp_path) -> private_runtime.PrivateAIRuntimeAdapter:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    return private_runtime.PrivateAIRuntimeAdapter()


def test_normalize_agent_turn_accepts_direct_and_wrapped_json() -> None:
    direct = private_runtime._normalize_agent_turn(
        {
            "customer_reply": "Hello.",
            "intent": "general_support",
            "next_action": "reply",
            "tool_calls": [],
        },
        max_output_chars=4000,
    )
    assert direct["customer_reply"] == "Hello."

    wrapped = private_runtime._normalize_agent_turn(
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
                                "arguments": {
                                    "tracking_number": "CH020000129135"
                                },
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
        private_runtime._parse_json_text("plain prose")


def test_prompt_contains_playbook_tool_and_observation_contract(
    monkeypatch,
    tmp_path,
) -> None:
    _configure(monkeypatch, tmp_path)
    request = _request(
        metadata={
            "customer_language": "en",
            "persona_context": {
                "assistant_name": "Nora",
                "token": "must-not-leak",
            },
            "agent_playbooks": [
                {
                    "name": "approved_knowledge",
                    "tools": ["knowledge.search"],
                }
            ],
            "agent_tools": [
                {
                    "name": "knowledge.search",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_observations": [
                {
                    "tool_name": "knowledge.search",
                    "ok": True,
                    "result": {"answer": "Approved"},
                }
            ],
            "agent_release_snapshot": _release_snapshot(),
        }
    )
    profile = private_runtime._resolve_profile(None, request)

    prompt = private_runtime._build_prompt(request, profile)

    assert "approved_knowledge" in prompt
    assert "knowledge.search" in prompt
    assert "Approved" in prompt
    assert "must-not-leak" not in prompt
    assert "nexus.agent_turn.v1" in prompt


@pytest.mark.asyncio
async def test_generate_accepts_final_agent_turn(monkeypatch, tmp_path) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        private_runtime,
        "_post_json",
        lambda profile, endpoint, payload, token: {
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
    assert result.raw_payload_safe_summary["agent_release_id"] == 7
    assert result.raw_payload_safe_summary["model_profile_version"] == 2


@pytest.mark.asyncio
async def test_generate_accepts_tool_request_without_customer_reply(
    monkeypatch,
    tmp_path,
) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        private_runtime,
        "_post_json",
        lambda profile, endpoint, payload, token: {
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
    assert (
        result.structured_output["tool_calls"][0]["tool_name"]
        == "speedaf.order.query"
    )


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
    monkeypatch.setattr(
        private_runtime,
        "_post_json",
        lambda profile, endpoint, payload, token: responses.pop(0),
    )

    result = await adapter.generate(None, _request())

    assert result.ok is True
    assert result.structured_output["next_action"] == (
        "ask_clarifying_question"
    )
    assert result.raw_payload_safe_summary["contract_repair_applied"] is True
    assert responses == []


@pytest.mark.asyncio
async def test_generate_fails_closed_without_release_model_profile(
    monkeypatch,
    tmp_path,
) -> None:
    adapter = _configure(monkeypatch, tmp_path)
    request = _request(metadata={"customer_language": "en"})

    result = await adapter.generate(None, request)

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_release_profile_required"


def test_production_requires_token_file(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_TOKEN", raising=False)

    profile = private_runtime._resolve_profile(None, _request())

    assert private_runtime._config_error(profile) == (
        "private_ai_runtime_token_file_required"
    )
