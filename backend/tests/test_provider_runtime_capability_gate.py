from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import app.services.provider_runtime as provider_runtime_package
from app.services.provider_runtime.adapters.capability_verified_private_ai_runtime import (
    CapabilityVerifiedPrivateAIRuntimeAdapter,
)
from app.services.provider_runtime.adapters.private_ai_runtime import (
    PrivateAIRuntimeAdapter,
)
from app.services.provider_runtime.registry import ProviderRegistry
from app.services.provider_runtime.runtime_capabilities import (
    CapabilityProbeResult,
    RuntimeCapabilityExpectations,
    evaluate_capability_manifest,
    parse_capability_manifest,
)
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


def request(
    *,
    output_contract: str = "nexus_webchat_runtime_reply_v1",
) -> ProviderRequest:
    return ProviderRequest(
        request_id="req-capability-gate-1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="session-capability-gate-1",
        scenario="webchat_runtime_reply",
        body="Hello",
        recent_context=[],
        tracking_fact_summary=None,
        tracking_fact_evidence_present=False,
        output_contract=output_contract,
        timeout_ms=8000,
        metadata={},
    )


def valid_manifest() -> dict:
    return {
        "schema": "nexus.ai_runtime.capabilities.v1",
        "runtime": {
            "id": "nexus-private-ai-runtime",
            "version": "2026.07.12.1",
        },
        "readiness": {"state": "ready", "reason_codes": []},
        "generation": {
            "model": "nexus-gemma4-e4b:latest",
            "structured_output": True,
            "api_path": "/api/chat",
            "request_contract": "ollama.chat.v1",
            "response_contract": "nexus_webchat_runtime_reply_v1",
        },
        "retrieval": {
            "enabled": True,
            "backend": "qdrant",
            "embedding_model": "qwen3-embedding",
            "embedding_dimension": 1024,
            "reranker_enabled": True,
            "reranker_model": "qwen3-reranker",
            "collection_alias": "nexus-knowledge-active",
        },
        "voice": {
            "stt": {"enabled": False, "model": None},
            "tts": {"enabled": False, "model": None},
            "live_voice": False,
        },
    }


def expectations() -> RuntimeCapabilityExpectations:
    return RuntimeCapabilityExpectations(
        schema="nexus.ai_runtime.capabilities.v1",
        runtime_id="nexus-private-ai-runtime",
        runtime_version="2026.07.12.1",
        generation_model="nexus-gemma4-e4b:latest",
        generation_api_path="/api/chat",
        request_contract="ollama.chat.v1",
        response_contract="nexus_webchat_runtime_reply_v1",
        retrieval_backend="qdrant",
        embedding_model="qwen3-embedding",
        embedding_dimension=1024,
        reranker_model="qwen3-reranker",
        collection_alias="nexus-knowledge-active",
    )


def ready_probe_result() -> CapabilityProbeResult:
    return evaluate_capability_manifest(
        parse_capability_manifest(json.dumps(valid_manifest())),
        expectations(),
    )


def configure_static_runtime(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    values = {
        "APP_ENV": "production",
        "PRIVATE_AI_RUNTIME_ENABLED": "true",
        "PRIVATE_AI_RUNTIME_BASE_URL": "http://ai-runtime.internal:18081",
        "PRIVATE_AI_RUNTIME_TOKEN_FILE": str(token_file),
        "PRIVATE_AI_RUNTIME_DIRECT_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_RAG_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_CHAT_MODE": "direct",
        "PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "ollama_chat",
        "PRIVATE_AI_RUNTIME_GENERATION_MODEL": "nexus-gemma4-e4b:latest",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL": (
            "nexus-gemma4-e4b:latest"
        ),
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT": "ollama.chat.v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT": (
            "nexus_webchat_runtime_reply_v1"
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_RAG_BASE_URL", raising=False)
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_DIRECT_MODEL", raising=False)
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_RAG_MODEL", raising=False)


def test_verified_adapter_uses_one_generation_model_for_direct_and_rag(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)

    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=ready_probe_result
    )

    assert adapter.generation_model == "nexus-gemma4-e4b:latest"
    assert adapter.direct_model == "nexus-gemma4-e4b:latest"
    assert adapter.rag_model == "nexus-gemma4-e4b:latest"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "qwen2.5:3b"),
        ("PRIVATE_AI_RUNTIME_RAG_MODEL", "qwen3:4b"),
        ("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "other-generation-model"),
    ],
)
def test_conflicting_legacy_model_configuration_fails_closed(
    monkeypatch,
    tmp_path: Path,
    name,
    value,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=ready_probe_result
    )

    assert (
        adapter._config_error()
        == "private_ai_runtime_legacy_model_configuration_invalid"
    )


@pytest.mark.parametrize(
    ("mutations", "reason_code"),
    [
        (
            {
                "PRIVATE_AI_RUNTIME_GENERATION_MODEL": "other-generation-model",
            },
            "capability_generation_model_mismatch",
        ),
        (
            {"PRIVATE_AI_RUNTIME_DIRECT_PATH": "/other-generation-path"},
            "capability_generation_contract_mismatch",
        ),
        (
            {"PRIVATE_AI_RUNTIME_REQUEST_SHAPE": "messages"},
            "capability_generation_contract_mismatch",
        ),
        (
            {
                "PRIVATE_AI_RUNTIME_CHAT_MODE": "rag",
                "PRIVATE_AI_RUNTIME_RAG_PATH": "/other-rag-path",
            },
            "capability_generation_contract_mismatch",
        ),
        (
            {
                "PRIVATE_AI_RUNTIME_CHAT_MODE": "rag",
                "PRIVATE_AI_RUNTIME_RAG_BASE_URL": "http://other-runtime.internal:18081",
            },
            "capability_runtime_identity_mismatch",
        ),
    ],
)
def test_local_generation_configuration_must_match_approved_expectation(
    monkeypatch,
    tmp_path: Path,
    mutations,
    reason_code,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    for name, value in mutations.items():
        monkeypatch.setenv(name, value)

    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=ready_probe_result
    )

    assert adapter._config_error() == reason_code


@pytest.mark.asyncio
async def test_output_contract_must_match_approved_expectation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    calls = []

    async def fake_generate(self, db, provider_request):
        calls.append(provider_request.request_id)
        raise AssertionError("base generation must not run")

    monkeypatch.setattr(PrivateAIRuntimeAdapter, "generate", fake_generate)
    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=ready_probe_result
    )

    result = await adapter.generate(
        Mock(),
        request(output_contract="unexpected_output_contract"),
    )

    assert calls == []
    assert result.ok is False
    assert result.error_code == "capability_generation_contract_mismatch"
    assert result.fallback_allowed is False


@pytest.mark.asyncio
async def test_capability_mismatch_blocks_underlying_generation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    calls = []

    async def fake_generate(self, db, provider_request):
        calls.append(provider_request.request_id)
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            model="nexus-gemma4-e4b:latest",
            elapsed_ms=1,
            raw_payload_safe_summary={"base_adapter_called": True},
            structured_output={"customer_reply": "unsafe if reached"},
            error_code=None,
            retryable=False,
            fallback_allowed=True,
        )

    monkeypatch.setattr(PrivateAIRuntimeAdapter, "generate", fake_generate)
    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=lambda: CapabilityProbeResult.not_ready(
            "capability_generation_model_mismatch"
        )
    )

    result = await adapter.generate(Mock(), request())

    assert calls == []
    assert result.ok is False
    assert result.error_code == "capability_generation_model_mismatch"
    assert result.structured_output is None
    assert result.fallback_allowed is False
    assert (
        result.raw_payload_safe_summary["runtime_capability"]["status"]
        == "not_ready"
    )
    rendered = json.dumps(result.raw_payload_safe_summary)
    assert "ai-runtime.internal" not in rendered
    assert "test-token" not in rendered


@pytest.mark.asyncio
async def test_ready_capability_calls_generation_and_attaches_safe_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    calls = []

    async def fake_generate(self, db, provider_request):
        calls.append(provider_request.request_id)
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            model=self.direct_model,
            elapsed_ms=3,
            raw_payload_safe_summary={"existing": "safe"},
            structured_output={"customer_reply": "Hello"},
            error_code=None,
            retryable=False,
            fallback_allowed=True,
        )

    monkeypatch.setattr(PrivateAIRuntimeAdapter, "generate", fake_generate)
    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=ready_probe_result
    )

    result = await adapter.generate(Mock(), request())

    assert calls == ["req-capability-gate-1"]
    assert result.ok is True
    assert result.model == "nexus-gemma4-e4b:latest"
    assert result.raw_payload_safe_summary["existing"] == "safe"
    capability = result.raw_payload_safe_summary["runtime_capability"]
    assert capability["status"] == "ready"
    assert capability["generation"]["model"] == "nexus-gemma4-e4b:latest"
    assert capability["retrieval"]["backend"] == "qdrant"


@pytest.mark.asyncio
async def test_static_configuration_failure_skips_capability_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "qwen2.5:3b")
    probe_calls = []
    adapter = CapabilityVerifiedPrivateAIRuntimeAdapter(
        capability_probe=(
            lambda: probe_calls.append(True) or ready_probe_result()
        )
    )

    result = await adapter.generate(Mock(), request())

    assert probe_calls == []
    assert result.ok is False
    assert (
        result.error_code
        == "private_ai_runtime_legacy_model_configuration_invalid"
    )
    assert result.fallback_allowed is False


def test_provider_registry_resolves_only_verified_adapter(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_static_runtime(monkeypatch, tmp_path)
    previous_factories = dict(ProviderRegistry._factories)
    previous_bootstrapped = provider_runtime_package._BOOTSTRAPPED
    try:
        ProviderRegistry._factories.clear()
        provider_runtime_package._BOOTSTRAPPED = False

        provider_runtime_package.bootstrap_provider_runtime()
        adapter = ProviderRegistry.get("private_ai_runtime", Mock())

        assert isinstance(adapter, CapabilityVerifiedPrivateAIRuntimeAdapter)
        assert type(adapter) is not PrivateAIRuntimeAdapter
    finally:
        ProviderRegistry._factories.clear()
        ProviderRegistry._factories.update(previous_factories)
        provider_runtime_package._BOOTSTRAPPED = previous_bootstrapped
