from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.api import admin_provider_runtime
from app.services.provider_runtime.runtime_capabilities import CapabilityProbeResult
from app.services.provider_runtime_capability_status import (
    get_provider_runtime_capability_expectation_status,
    probe_provider_runtime_capabilities,
)
from app.services.provider_runtime_status import _private_ai_runtime_status_from_env

_REPO_ROOT = Path(__file__).resolve().parents[2]


def configure_expectations(monkeypatch) -> None:
    values = {
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID": "nexus-private-ai-runtime",
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION": "2026.07.12.1",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL": "nexus-gemma4-e4b:latest",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH": "/api/chat",
        "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT": "ollama.chat.v1",
        "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT": (
            "nexus_webchat_runtime_reply_v1"
        ),
        "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND": "qdrant",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL": "qwen3-embedding",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION": "1024",
        "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL": "qwen3-reranker",
        "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS": "nexus-knowledge-active",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_expectation_status_is_bounded_and_secret_free(monkeypatch) -> None:
    configure_expectations(monkeypatch)
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "http://runtime.internal:18081",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "/run/secrets/private-ai-runtime-token",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "must-not-leak")

    status = get_provider_runtime_capability_expectation_status()

    assert status == {
        "schema": "nexus.ai_runtime.capability_expectation.v1",
        "status": "ready",
        "reason_codes": [],
        "expected": {
            "capability_schema": "nexus.ai_runtime.capabilities.v1",
            "runtime": {
                "id": "nexus-private-ai-runtime",
                "version": "2026.07.12.1",
            },
            "generation": {
                "model": "nexus-gemma4-e4b:latest",
                "api_path": "/api/chat",
                "request_contract": "ollama.chat.v1",
                "response_contract": "nexus_webchat_runtime_reply_v1",
            },
            "retrieval": {
                "backend": "qdrant",
                "embedding_model": "qwen3-embedding",
                "embedding_dimension": 1024,
                "reranker_model": "qwen3-reranker",
                "collection_alias": "nexus-knowledge-active",
            },
        },
        "boundary": {
            "external_network_call": False,
            "secret_values_exposed": False,
            "internal_endpoint_exposed": False,
        },
    }
    rendered = json.dumps(status)
    assert "runtime.internal" not in rendered
    assert "private-ai-runtime-token" not in rendered
    assert "must-not-leak" not in rendered


def test_missing_expectation_is_not_ready_without_env_name_leak(monkeypatch) -> None:
    configure_expectations(monkeypatch)
    monkeypatch.delenv("PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION")

    status = get_provider_runtime_capability_expectation_status()

    assert status["status"] == "not_ready"
    assert status["reason_codes"] == ["capability_expectation_missing"]
    assert status["expected"] is None
    assert "PRIVATE_AI_RUNTIME" not in json.dumps(status)


def test_live_probe_passes_only_server_side_configuration(monkeypatch) -> None:
    configure_expectations(monkeypatch)
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "http://runtime.internal:18081",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "/run/secrets/private-ai-runtime-token",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_CAPABILITIES_PATH",
        "/v1/capabilities",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS", "3")
    calls = []

    def fake_probe(**kwargs):
        calls.append(kwargs)
        return CapabilityProbeResult.not_ready(
            "capability_runtime_version_mismatch"
        )

    status = probe_provider_runtime_capabilities(probe_fn=fake_probe)

    assert status["status"] == "not_ready"
    assert status["reason_codes"] == ["capability_runtime_version_mismatch"]
    assert calls[0]["base_url"] == "http://runtime.internal:18081"
    assert calls[0]["token_file"] == "/run/secrets/private-ai-runtime-token"
    assert calls[0]["capabilities_path"] == "/v1/capabilities"
    assert calls[0]["timeout_seconds"] == 3.0
    rendered = json.dumps(status)
    assert "runtime.internal" not in rendered
    assert "private-ai-runtime-token" not in rendered


@pytest.mark.parametrize(
    ("name", "value", "reason_code"),
    [
        ("PRIVATE_AI_RUNTIME_BASE_URL", "", "capability_endpoint_invalid"),
        ("PRIVATE_AI_RUNTIME_TOKEN_FILE", "", "capability_token_missing"),
        (
            "PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS",
            "invalid",
            "capability_expectation_invalid",
        ),
    ],
)
def test_live_probe_static_configuration_fails_closed(
    monkeypatch,
    name,
    value,
    reason_code,
) -> None:
    configure_expectations(monkeypatch)
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "http://runtime.internal:18081",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "/run/secrets/private-ai-runtime-token",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv(name, value)
    probe_calls = []

    status = probe_provider_runtime_capabilities(
        probe_fn=lambda **kwargs: (
            probe_calls.append(kwargs)
            or CapabilityProbeResult.not_ready("unexpected")
        )
    )

    assert probe_calls == []
    assert status["status"] == "not_ready"
    assert status["reason_codes"] == [reason_code]


def test_active_status_uses_generation_model_not_direct_and_rag_models(
    monkeypatch,
) -> None:
    configure_expectations(monkeypatch)
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
        "nexus-gemma4-e4b:latest",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_DIRECT_MODEL",
        "nexus-gemma4-e4b:latest",
    )
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_RAG_MODEL",
        "nexus-gemma4-e4b:latest",
    )

    status = _private_ai_runtime_status_from_env()

    assert status["generation_model"] == "nexus-gemma4-e4b:latest"
    assert status["legacy_model_configuration_valid"] is True
    assert "direct_model" not in status
    assert "rag_model" not in status
    assert status["capability_expectation"]["status"] == "ready"


def test_active_status_rejects_stale_legacy_qwen_values(monkeypatch) -> None:
    configure_expectations(monkeypatch)
    monkeypatch.setenv(
        "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
        "nexus-gemma4-e4b:latest",
    )
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_RAG_MODEL", "qwen3:4b")

    status = _private_ai_runtime_status_from_env()

    assert status["legacy_model_configuration_valid"] is False
    assert status["configured"] is False


def test_admin_explicit_probe_is_read_only_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_provider_runtime,
        "ensure_can_manage_runtime",
        lambda user, db: None,
    )
    monkeypatch.setattr(
        admin_provider_runtime,
        "probe_provider_runtime_capabilities",
        lambda: {
            "schema": "nexus.ai_runtime.capability_probe.v1",
            "status": "not_ready",
            "reason_codes": ["capability_runtime_version_mismatch"],
            "boundary": {
                "secret_values_exposed": False,
                "internal_endpoint_exposed": False,
                "raw_manifest_exposed": False,
            },
        },
    )

    response = admin_provider_runtime.provider_runtime_capabilities_probe(
        db=Mock(),
        current_user=Mock(),
    )

    assert response["status"] == "not_ready"
    assert response["reason_codes"] == ["capability_runtime_version_mismatch"]
    assert response["boundary"]["secret_values_exposed"] is False


def test_active_runtime_artifacts_have_no_stale_qwen_generation_authority() -> None:
    paths = [
        _REPO_ROOT / "deploy/.env.candidate.example",
        _REPO_ROOT / "deploy/.env.prod.example",
        _REPO_ROOT / "backend/scripts/smoke_private_ai_runtime.py",
        _REPO_ROOT / "scripts/smoke/warm_private_ai_runtime.py",
        _REPO_ROOT / "docs/ops/PRIVATE_AI_RUNTIME_ROLLOUT_RUNBOOK.md",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "qwen2.5:3b" not in text, path
        assert "qwen3:4b" not in text, path


def test_candidate_templates_require_exact_capability_identity() -> None:
    required_keys = {
        "PRIVATE_AI_RUNTIME_CAPABILITIES_PATH",
        "PRIVATE_AI_RUNTIME_GENERATION_MODEL",
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID",
        "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL",
        "PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH",
        "PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT",
        "PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT",
        "PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL",
        "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION",
        "PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL",
        "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS",
    }
    for relative_path in (
        "deploy/.env.candidate.example",
        "deploy/.env.prod.example",
    ):
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        keys = {
            line.split("=", 1)[0]
            for line in text.splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        assert required_keys <= keys
        assert "PRIVATE_AI_RUNTIME_DIRECT_MODEL" not in keys
        assert "PRIVATE_AI_RUNTIME_RAG_MODEL" not in keys
        assert "PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION=\n" in text
        assert "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION=\n" in text
        assert "PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS=\n" in text


def test_provider_runtime_gate_runs_capability_suite_and_route_contract() -> None:
    workflow = (
        _REPO_ROOT / ".github/workflows/provider-runtime-gate.yml"
    ).read_text(encoding="utf-8")

    for test_name in (
        "test_provider_runtime_capabilities.py",
        "test_private_ai_runtime_capability_endpoint.py",
        "test_provider_runtime_capability_gate.py",
        "test_provider_runtime_capability_status.py",
    ):
        assert test_name in workflow
    assert "/api/admin/provider-runtime/capabilities/probe" in workflow
    assert "qwen2\\.5:3b|qwen3:4b" in workflow


def test_rollout_runbook_contains_no_literal_public_runtime_ipv4() -> None:
    runbook = (
        _REPO_ROOT / "docs/ops/PRIVATE_AI_RUNTIME_ROLLOUT_RUNBOOK.md"
    ).read_text(encoding="utf-8")

    assert not re.search(r"https?://(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", runbook)
    assert "nexus.ai_runtime.capabilities.v1" in runbook
    assert "PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION" in runbook
