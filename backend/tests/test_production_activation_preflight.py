from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "validate_production_activation.py"
SPEC = importlib.util.spec_from_file_location("validate_production_activation", SCRIPT)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
CONTROLLED_IMAGE = f"ghcr.io/maximvonshaft/nexus_helpdesk@{IMAGE_DIGEST}"


def _candidate_binding() -> dict[str, str]:
    return {
        "GIT_SHA": SOURCE_SHA,
        "CONTROLLED_IMAGE": CONTROLLED_IMAGE,
        "ACTIVATION_EVIDENCE_SOURCE_SHA": SOURCE_SHA,
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST": IMAGE_DIGEST,
    }


def _full_values() -> dict[str, str]:
    return {
        **_candidate_binding(),
        "PRODUCTION_PROFILE": "full",
        "PROVIDER_RUNTIME_ENABLED": "true",
        "PROVIDER_RUNTIME_TRAFFIC_MODE": "full",
        "PROVIDER_RUNTIME_KILL_SWITCH": "false",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "100",
        "WEBCHAT_AI_ENABLED": "true",
        "WEBCHAT_AI_AUTO_REPLY_MODE": "runtime",
        "WEBCHAT_HUMAN_CALL_ENABLED": "false",
        "WEBCHAT_LIVE_AI_VOICE_ENABLED": "false",
        "ENABLE_OUTBOUND_DISPATCH": "false",
        "OUTBOUND_PROVIDER": "disabled",
        "OPERATIONS_DISPATCH_MODE": "disabled",
        "OPERATIONS_DISPATCH_ADAPTER": "disabled",
        "PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/production",
        "WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/webchat-ai",
    }


def test_full_activation_passes_with_exact_controls_and_evidence() -> None:
    result = activation.validate(_full_values())

    assert result["schema"] == "nexus.production-activation-preflight.v2"
    assert result["status"] == "pass"
    assert result["profile"] == "full"
    assert result["candidate"] == {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
    }
    assert result["capabilities"]["webchat_ai"] is True
    assert result["evidence"] == {
        "production": "https://evidence.example/production",
        "webchat_ai": "https://evidence.example/webchat-ai",
    }
    assert result["contains_secrets"] is False
    assert result["external_effects_performed"] is False


def test_full_activation_fails_closed_without_production_evidence() -> None:
    values = _full_values()
    values.pop("PRODUCTION_E2E_EVIDENCE_URL")

    with pytest.raises(activation.ActivationError, match="evidence_missing"):
        activation.validate(values)


def test_activation_rejects_placeholder_and_wrong_candidate_binding() -> None:
    values = _full_values()
    values["PRODUCTION_E2E_EVIDENCE_URL"] = (
        "https://github.com/Maximvonshaft/nexus_helpdesk/actions/runs/<run-id>"
    )
    with pytest.raises(activation.ActivationError, match="evidence_missing"):
        activation.validate(values)

    values = _full_values()
    values["ACTIVATION_EVIDENCE_SOURCE_SHA"] = "c" * 40
    with pytest.raises(
        activation.ActivationError,
        match="activation_evidence_source_sha_mismatch",
    ):
        activation.validate(values)

    values = _full_values()
    values["ACTIVATION_EVIDENCE_IMAGE_DIGEST"] = "sha256:" + "d" * 64
    with pytest.raises(
        activation.ActivationError,
        match="activation_evidence_image_digest_mismatch",
    ):
        activation.validate(values)


def test_environment_mode_uses_the_ephemeral_container_environment(monkeypatch) -> None:
    values = _full_values()
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    args = argparse.Namespace(environment=True, env_file=None)
    observed = activation._input_values(args)

    assert observed["GIT_SHA"] == SOURCE_SHA
    assert activation.validate(observed)["status"] == "pass"


def test_input_modes_are_mutually_exclusive() -> None:
    args = argparse.Namespace(
        environment=True,
        env_file=[Path("activation.env")],
    )
    with pytest.raises(
        activation.ActivationError,
        match="activation_input_modes_conflict",
    ):
        activation._input_values(args)


def test_voice_activation_requires_livekit_credentials_models_and_evidence() -> None:
    values = _full_values()
    values.update(
        {
            "WEBCHAT_HUMAN_CALL_ENABLED": "true",
            "WEBCHAT_LIVE_AI_VOICE_ENABLED": "true",
            "WEBCHAT_VOICE_PROVIDER": "livekit",
            "LIVEKIT_URL": "wss://voice.example.test",
            "LIVEKIT_WEBHOOK_ENABLED": "true",
            "LIVEKIT_AGENT_NAME": "nexus-voice-agent",
            "LIVEKIT_API_KEY_FILE": "/run/secrets/livekit_api_key",
            "LIVEKIT_API_SECRET_FILE": "/run/secrets/livekit_api_secret",
            "LIVEKIT_AGENT_SHARED_SECRET_FILE": "/run/secrets/livekit_agent_shared_secret",
            "NEXUS_VOICE_STT_MODEL": "stt-model",
            "NEXUS_VOICE_TTS_MODEL": "tts-model",
            "TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/telephony",
        }
    )

    assert activation.validate(values)["capabilities"]["voice"] is True

    values["TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL"] = ""
    with pytest.raises(activation.ActivationError, match="evidence_missing"):
        activation.validate(values)


def test_provider_canary_rejects_unbounded_or_parallel_external_effects() -> None:
    values = {
        **_candidate_binding(),
        "PRODUCTION_PROFILE": "provider_canary",
        "PROVIDER_RUNTIME_ENABLED": "true",
        "PROVIDER_RUNTIME_TRAFFIC_MODE": "canary",
        "PROVIDER_RUNTIME_KILL_SWITCH": "false",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "5",
        "PROVIDER_CANARY_E2E_EVIDENCE_URL": "https://evidence.example/provider-canary",
        "ENABLE_OUTBOUND_DISPATCH": "false",
        "OUTBOUND_PROVIDER": "disabled",
        "WEBCHAT_HUMAN_CALL_ENABLED": "false",
        "WEBCHAT_LIVE_AI_VOICE_ENABLED": "false",
        "OPERATIONS_DISPATCH_MODE": "disabled",
        "OPERATIONS_DISPATCH_ADAPTER": "disabled",
    }
    result = activation.validate(values)
    assert result["status"] == "pass"
    assert result["evidence"] == {
        "provider_canary": "https://evidence.example/provider-canary"
    }

    values["PROVIDER_RUNTIME_CANARY_PERCENT"] = "50"
    with pytest.raises(
        activation.ActivationError,
        match="provider_canary_percent_invalid",
    ):
        activation.validate(values)
