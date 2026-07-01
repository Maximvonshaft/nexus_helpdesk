from __future__ import annotations

import json
import urllib.error
from unittest.mock import Mock

import pytest

from app.services.provider_runtime.adapters.private_ai_runtime import PrivateAIRuntimeAdapter
from app.services.provider_runtime.schemas import ProviderRequest


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-private-ai-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess-private-ai-1",
        "scenario": "webchat_fast_reply",
        "body": "Where is my parcel?",
        "recent_context": [{"role": "user", "content": "hello"}],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 8000,
        "metadata": {
            "knowledge_context": {"hits": [], "raw_payload": "must-not-leak"},
            "persona_context": {"name": "Speedaf Assistant", "secret": "must-not-leak"},
        },
    }
    data.update(overrides)
    return ProviderRequest(**data)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in [
        "APP_ENV",
        "PRIVATE_AI_RUNTIME_ENABLED",
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "PRIVATE_AI_RUNTIME_TOKEN",
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "PRIVATE_AI_RUNTIME_DIRECT_PATH",
        "PRIVATE_AI_RUNTIME_RAG_PATH",
        "PRIVATE_AI_RUNTIME_CHAT_MODE",
        "PRIVATE_AI_RUNTIME_REQUEST_SHAPE",
        "PRIVATE_AI_RUNTIME_DIRECT_MODEL",
        "PRIVATE_AI_RUNTIME_RAG_MODEL",
        "PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS",
        "PRIVATE_AI_RUNTIME_MAX_PROMPT_CHARS",
        "PRIVATE_AI_RUNTIME_MAX_OUTPUT_CHARS",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_private_ai_runtime_success_normalizes_response_text(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint == "http://ai-runtime.internal:18081/chat/direct"
        assert token == "test-token"
        return {
            "response_text": "Please share your tracking number so I can check this safely.",
            "intent": "tracking",
            "handoff_required": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request())

    assert result.ok is True
    assert result.provider == "private_ai_runtime"
    assert result.model == "qwen2.5:3b"
    assert result.structured_output["customer_reply"] == "Please share your tracking number so I can check this safely."
    assert result.structured_output["intent"] == "tracking_missing_number"
    assert result.raw_payload_safe_summary["endpoint_path"] == "/chat/direct"
    assert result.raw_payload_safe_summary["token_file_configured"] is True
    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert "must-not-leak" not in rendered


@pytest.mark.asyncio
async def test_private_ai_runtime_rag_mode_uses_rag_model(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CHAT_MODE", "rag")
    adapter = PrivateAIRuntimeAdapter()

    def fake_post_json(endpoint, payload, token):
        assert endpoint == "http://ai-runtime.internal:18081/chat/rag"
        assert payload["model"] == "qwen3:4b"
        return {
            "customer_reply": "Address changes must be verified by a support agent.",
            "language": "en",
            "intent": "address_change",
            "handoff_required": True,
            "handoff_reason": "manual verification required",
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Can you change my delivery address?"))

    assert result.ok is True
    assert result.model == "qwen3:4b"
    assert result.structured_output["handoff_required"] is True


@pytest.mark.asyncio
async def test_private_ai_runtime_question_shape_matches_runtime_contract(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint == "http://ai-runtime.internal:18081/chat/direct"
        assert token == "test-token"
        return {
            "status": "ok",
            "answer": "Please share your tracking number so I can check this safely.",
            "raw_content": "Please share your tracking number so I can check this safely.",
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request())

    assert result.ok is True
    assert captured_payload["model"] == "qwen2.5:3b"
    assert "question" in captured_payload
    assert "input" not in captured_payload
    assert "messages" not in captured_payload
    assert result.raw_payload_safe_summary["request_shape"] == "question"
    assert result.structured_output["customer_reply"] == "Please share your tracking number so I can check this safely."


@pytest.mark.asyncio
async def test_private_ai_runtime_production_rejects_inline_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "inline-token")

    result = await PrivateAIRuntimeAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_inline_token_forbidden"
    assert "inline-token" not in json.dumps(result.model_dump(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_private_ai_runtime_http_429_is_retryable(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()

    def fake_post_json(endpoint, payload, token):
        raise urllib.error.HTTPError(url=endpoint, code=429, msg="rate limited", hdrs=None, fp=None)

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_http_429"
    assert result.retryable is True
    assert result.raw_payload_safe_summary["retryable_http"] is True
