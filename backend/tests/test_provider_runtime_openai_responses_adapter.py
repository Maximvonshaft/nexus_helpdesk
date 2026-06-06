from __future__ import annotations

import json
import urllib.error
from unittest.mock import Mock

import pytest

from app.services.provider_runtime.adapters.openai_responses import OpenAIResponsesAdapter
from app.services.provider_runtime.schemas import ProviderRequest


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-openai-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess-openai-1",
        "scenario": "webchat_fast_reply",
        "body": "Where is my parcel?",
        "recent_context": [{"role": "user", "content": "old context should be bounded"}],
        "tracking_fact_summary": "Trusted tracking fact: status=in transit.",
        "tracking_fact_evidence_present": True,
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 8000,
        "metadata": {
            "knowledge_context": {"hits": [], "raw_payload": "must-not-leak"},
            "persona_context": {"name": "Speedaf Assistant", "secret": "must-not-leak"},
        },
    }
    data.update(overrides)
    return ProviderRequest(**data)


def _strict_reply(**overrides):
    data = {
        "customer_reply": "Your parcel is currently in transit.",
        "language": "en",
        "intent": "tracking",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
        "ticket_should_create": False,
        "tool_calls": [],
        "evidence_used": [
            {"source": "tracking_fact", "source_id": None, "snippet": "status=in transit", "fact_evidence_present": True}
        ],
        "confidence": 0.92,
        "reason": "trusted tracking fact present",
        "risk_level": "low",
        "next_action": "reply",
        "safety_notes": [],
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in [
        "OPENAI_RESPONSES_MODEL",
        "OPENAI_RESPONSES_BASE_URL",
        "OPENAI_RESPONSES_TIMEOUT_SECONDS",
        "OPENAI_RESPONSES_MAX_PROMPT_CHARS",
        "OPENAI_RESPONSES_MAX_OUTPUT_TOKENS",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_openai_responses_missing_api_key_is_unavailable():
    result = await OpenAIResponsesAdapter("").generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "not_configured"
    assert result.fallback_allowed is True
    assert result.raw_payload_safe_summary["api_key_present"] is False


@pytest.mark.asyncio
async def test_openai_responses_success_normalizes_structured_output(monkeypatch):
    adapter = OpenAIResponsesAdapter("sk-test")
    captured_payload = {}

    def fake_post_json(payload):
        captured_payload.update(payload)
        return {
            "id": "resp_123",
            "status": "completed",
            "output_text": json.dumps(_strict_reply()),
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request())

    assert result.ok is True
    assert result.provider == "openai_responses"
    assert result.structured_output["customer_reply"] == "Your parcel is currently in transit."
    assert result.structured_output["reply"] == "Your parcel is currently in transit."
    assert result.structured_output["intent"] == "tracking"
    assert result.raw_payload_safe_summary["response_id"] == "resp_123"
    assert result.raw_payload_safe_summary["usage"] == {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    assert captured_payload["model"] == "gpt-4o-mini"
    assert captured_payload["store"] is False
    assert captured_payload["text"]["format"]["type"] == "json_schema"
    prompt_blob = json.dumps(captured_payload, ensure_ascii=False)
    assert "must-not-leak" not in prompt_blob


@pytest.mark.asyncio
async def test_openai_responses_bad_json_is_retryable(monkeypatch):
    adapter = OpenAIResponsesAdapter("sk-test")
    monkeypatch.setattr(adapter, "_post_json", lambda payload: {"output_text": "not-json"})

    result = await adapter.generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "openai_responses_bad_json"
    assert result.retryable is True
    assert result.fallback_allowed is True


@pytest.mark.asyncio
async def test_openai_responses_http_429_is_retryable(monkeypatch):
    adapter = OpenAIResponsesAdapter("sk-test")

    def fake_post_json(payload):
        raise urllib.error.HTTPError(
            url="https://api.openai.com/v1/responses",
            code=429,
            msg="rate limited",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "openai_responses_http_429"
    assert result.retryable is True
    assert result.raw_payload_safe_summary["retryable_http"] is True


@pytest.mark.asyncio
async def test_openai_responses_timeout_is_failover_worthy(monkeypatch):
    adapter = OpenAIResponsesAdapter("sk-test")
    monkeypatch.setattr(adapter, "_post_json", lambda payload: (_ for _ in ()).throw(TimeoutError()))

    result = await adapter.generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "openai_responses_timeout"
    assert result.retryable is True
    assert result.raw_payload_safe_summary["timeout_seconds"] == 8
