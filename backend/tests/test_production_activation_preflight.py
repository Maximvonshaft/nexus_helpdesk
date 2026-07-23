from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "validate_production_activation.py"
SPEC = importlib.util.spec_from_file_location("validate_production_activation", SCRIPT)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


def _full_values() -> dict[str, str]:
    return {
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

    assert result["status"] == "pass"
    assert result["profile"] == "full"
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
    assert activation.validate(values)["status"] == "pass"

    values["PROVIDER_RUNTIME_CANARY_PERCENT"] = "50"
    with pytest.raises(activation.ActivationError, match="provider_canary_percent_invalid"):
        activation.validate(values)
