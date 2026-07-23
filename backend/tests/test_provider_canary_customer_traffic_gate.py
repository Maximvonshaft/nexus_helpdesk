from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import app.services.release_readiness as readiness

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "validate_production_activation.py"
SPEC = importlib.util.spec_from_file_location("validate_production_activation", SCRIPT)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


def test_runtime_readiness_rejects_customer_ai_during_provider_canary(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROVIDER_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "5")
    monkeypatch.setenv("WEBCHAT_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "false")
    monkeypatch.setenv("OUTBOUND_PROVIDER", "disabled")
    monkeypatch.setenv("OPERATIONS_DISPATCH_MODE", "disabled")

    result = readiness._configuration_snapshot("provider_canary")

    assert result["status"] == "not_ready"
    assert "canary_webchat_ai_must_remain_disabled" in result["reason_codes"]


def test_activation_preflight_rejects_customer_ai_during_provider_canary() -> None:
    source_sha = "a" * 40
    image_digest = "sha256:" + "b" * 64
    values = {
        "GIT_SHA": source_sha,
        "CONTROLLED_IMAGE": f"ghcr.io/nexus@{image_digest}",
        "ACTIVATION_EVIDENCE_SOURCE_SHA": source_sha,
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST": image_digest,
        "PRODUCTION_PROFILE": "provider_canary",
        "PROVIDER_RUNTIME_ENABLED": "true",
        "PROVIDER_RUNTIME_TRAFFIC_MODE": "canary",
        "PROVIDER_RUNTIME_KILL_SWITCH": "false",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "5",
        "PROVIDER_CANARY_E2E_EVIDENCE_URL": (
            "https://evidence.example/provider-canary"
        ),
        "WEBCHAT_AI_ENABLED": "true",
        "WEBCHAT_HUMAN_CALL_ENABLED": "false",
        "WEBCHAT_LIVE_AI_VOICE_ENABLED": "false",
        "ENABLE_OUTBOUND_DISPATCH": "false",
        "OUTBOUND_PROVIDER": "disabled",
        "OPERATIONS_DISPATCH_MODE": "disabled",
        "OPERATIONS_DISPATCH_ADAPTER": "disabled",
    }

    with pytest.raises(
        activation.ActivationError,
        match="provider_canary_webchat_ai_forbidden",
    ):
        activation.validate(values)
