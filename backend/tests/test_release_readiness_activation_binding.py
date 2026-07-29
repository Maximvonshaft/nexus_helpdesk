from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.activation_evidence_policy import (
    activation_evidence_snapshot,
    finalize_release_readiness,
)
from app.services.activation_runtime_configuration import (
    activation_runtime_configuration_digest,
)

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/maximvonshaft/nexus_helpdesk@{IMAGE_DIGEST}"
ENVIRONMENT_ID = "production-eu-1"
KEY_ID = "activation-test-2026-01"
PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY_PEM = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")


def _identity() -> dict[str, object]:
    return {
        "status": "ready",
        "reason_codes": [],
        "source_sha": SOURCE_SHA,
        "image": IMAGE,
    }


def _configuration(**overrides) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "ready",
        "reason_codes": [],
        "provider": {
            "enabled": True,
            "mode": "full",
            "kill_switch": False,
            "canary_percent": 100,
        },
        "webchat_ai_enabled": False,
        "voice_enabled": False,
        "outbound": {"enabled": False, "provider": "disabled"},
        "whatsapp": {
            "enabled": False,
            "media_enabled": False,
            "media_scanner": "disabled",
        },
        "operations_mode": "disabled",
    }
    result.update(overrides)
    return result


def _runtime_environment(profile: str) -> dict[str, str]:
    if profile == "provider_canary":
        return {
            "PRODUCTION_PROFILE": profile,
            "PROVIDER_RUNTIME_ENABLED": "true",
            "PROVIDER_RUNTIME_TRAFFIC_MODE": "canary",
            "PROVIDER_RUNTIME_KILL_SWITCH": "false",
            "PROVIDER_RUNTIME_CANARY_PERCENT": "5",
            "WEBCHAT_AI_ENABLED": "false",
            "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
            "ENABLE_OUTBOUND_DISPATCH": "false",
            "OUTBOUND_PROVIDER": "disabled",
            "OPERATIONS_DISPATCH_MODE": "disabled",
            "OPERATIONS_DISPATCH_ADAPTER": "disabled",
        }
    return {
        "PRODUCTION_PROFILE": "full",
        "PROVIDER_RUNTIME_ENABLED": "true",
        "PROVIDER_RUNTIME_TRAFFIC_MODE": "full",
        "PROVIDER_RUNTIME_KILL_SWITCH": "false",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "100",
        "WEBCHAT_AI_ENABLED": "false",
        "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
        "ENABLE_OUTBOUND_DISPATCH": "false",
        "OUTBOUND_PROVIDER": "disabled",
        "OPERATIONS_DISPATCH_MODE": "disabled",
        "OPERATIONS_DISPATCH_ADAPTER": "disabled",
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_environment(
    references: dict[str, str],
    *,
    profile: str | None = None,
    runtime_overrides: dict[str, str] | None = None,
    private_key: Ed25519PrivateKey = PRIVATE_KEY,
    public_key_pem: str = PUBLIC_KEY_PEM,
    key_id: str = KEY_ID,
    **overrides: str,
) -> dict[str, str]:
    resolved_profile = profile or (
        "provider_canary"
        if "PROVIDER_CANARY_E2E_EVIDENCE_URL" in references
        else "full"
    )
    result = {
        "APP_ENV": "test",
        **_runtime_environment(resolved_profile),
        "ACTIVATION_EVIDENCE_SOURCE_SHA": SOURCE_SHA,
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST": IMAGE_DIGEST,
        "ACTIVATION_EVIDENCE_ENVIRONMENT_ID": ENVIRONMENT_ID,
        "ACTIVATION_EVIDENCE_VERIFICATION_KEY": public_key_pem,
        **references,
    }
    if runtime_overrides:
        result.update(runtime_overrides)
    result.update(overrides)
    configuration_digest = activation_runtime_configuration_digest(
        profile=resolved_profile,
        environment=result,
    )
    result["ACTIVATION_EVIDENCE_CONFIGURATION_DIGEST"] = configuration_digest

    candidate = {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
        "configuration_digest": configuration_digest,
        "environment_id": ENVIRONMENT_ID,
    }
    generated_at = datetime.now(timezone.utc).isoformat()
    evidence = {
        key.lower(): {
            "url": url,
            "result": "pass",
            "artifact_sha256": "sha256:" + hashlib.sha256(key.encode()).hexdigest(),
            "source_sha": SOURCE_SHA,
            "image_digest": IMAGE_DIGEST,
            "configuration_digest": configuration_digest,
            "environment_id": ENVIRONMENT_ID,
            "generated_at": generated_at,
        }
        for key, url in references.items()
    }
    unsigned = {
        "schema": "nexus.activation-evidence.v3",
        "candidate": candidate,
        "evidence": evidence,
    }
    signature = base64.urlsafe_b64encode(
        private_key.sign(_canonical(unsigned))
    ).decode("ascii").rstrip("=")
    result["ACTIVATION_EVIDENCE_MANIFEST_JSON"] = json.dumps(
        {
            **unsigned,
            "signature": {
                "algorithm": "ed25519",
                "key_id": key_id,
                "value": signature,
            },
        }
    )
    return result


def test_controlled_profile_never_requires_external_activation_evidence() -> None:
    result = activation_evidence_snapshot(
        profile="controlled",
        configuration=_configuration(),
        identity=_identity(),
        environment={},
    )
    assert result["status"] == "ready"
    assert result["required"] == []
    assert result["candidate"] is None
    assert result["reason_codes"] == []
    assert result["manifest_sha256"] is None


def test_provider_canary_rejects_url_without_verified_manifest() -> None:
    result = activation_evidence_snapshot(
        profile="provider_canary",
        configuration=_configuration(),
        identity=_identity(),
        environment={
            **_runtime_environment("provider_canary"),
            "ACTIVATION_EVIDENCE_SOURCE_SHA": SOURCE_SHA,
            "ACTIVATION_EVIDENCE_IMAGE_DIGEST": IMAGE_DIGEST,
            "PROVIDER_CANARY_E2E_EVIDENCE_URL": (
                "https://evidence.example/provider-canary"
            ),
        },
    )
    assert result["status"] == "not_ready"
    assert "activation_evidence_configuration_digest_invalid" in result["reason_codes"]
    assert "activation_evidence_environment_id_invalid" in result["reason_codes"]
    assert "activation_evidence_manifest_missing" in result["reason_codes"]


def test_provider_canary_requires_verified_candidate_bound_manifest() -> None:
    reference = {
        "PROVIDER_CANARY_E2E_EVIDENCE_URL": (
            "https://evidence.example/provider-canary"
        )
    }
    environment = _signed_environment(reference, profile="provider_canary")
    expected_digest = activation_runtime_configuration_digest(
        profile="provider_canary",
        environment=environment,
    )
    result = activation_evidence_snapshot(
        profile="provider_canary",
        configuration=_configuration(),
        identity=_identity(),
        environment=environment,
    )
    assert result["status"] == "ready"
    assert result["schema"] == "nexus.activation-evidence.v3"
    assert result["required"] == ["provider_canary_e2e_evidence_url"]
    assert result["candidate"] == {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
        "runtime_source_sha": SOURCE_SHA,
        "runtime_image_digest": IMAGE_DIGEST,
        "configuration_digest": expected_digest,
        "environment_id": ENVIRONMENT_ID,
    }
    assert result["manifest_sha256"].startswith("sha256:")
    assert result["receipts"]["provider_canary_e2e_evidence_url"]["result"] == "pass"


def test_manifest_rejects_tampering_wrong_public_key_and_signing_material() -> None:
    references = {
        "PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/production"
    }
    environment = _signed_environment(references)
    manifest = json.loads(environment["ACTIVATION_EVIDENCE_MANIFEST_JSON"])
    manifest["evidence"]["production_e2e_evidence_url"]["result"] = "failed"
    environment["ACTIVATION_EVIDENCE_MANIFEST_JSON"] = json.dumps(manifest)
    tampered = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=environment,
    )
    assert "activation_evidence_signature_mismatch" in tampered["reason_codes"]

    other_private = Ed25519PrivateKey.generate()
    other_public = other_private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    wrong_key = _signed_environment(references, public_key_pem=other_public)
    wrong_key_result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=wrong_key,
    )
    assert "activation_evidence_signature_mismatch" in wrong_key_result["reason_codes"]

    forbidden = _signed_environment(references)
    forbidden["ACTIVATION_EVIDENCE_SIGNING_KEY"] = "runtime-cannot-sign"
    forbidden_result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=forbidden,
    )
    assert "activation_evidence_signing_material_forbidden" in forbidden_result["reason_codes"]


def test_signed_manifest_rejects_wrong_candidate_and_configuration_drift() -> None:
    references = {
        "PROVIDER_CANARY_E2E_EVIDENCE_URL": (
            "https://evidence.example/provider-canary"
        )
    }
    wrong_candidate = _signed_environment(
        {"PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/production"},
        ACTIVATION_EVIDENCE_SOURCE_SHA="d" * 40,
    )
    wrong_result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=wrong_candidate,
    )
    assert "activation_evidence_source_sha_mismatch" in wrong_result["reason_codes"]

    environment = _signed_environment(references, profile="provider_canary")
    environment["PROVIDER_RUNTIME_CANARY_PERCENT"] = "6"
    drift = activation_evidence_snapshot(
        profile="provider_canary",
        configuration=_configuration(),
        identity=_identity(),
        environment=environment,
    )
    assert drift["status"] == "not_ready"
    assert "activation_evidence_configuration_digest_mismatch" in drift["reason_codes"]
    assert "activation_evidence_manifest_configuration_digest_mismatch" in drift["reason_codes"]


def test_livekit_model_drift_invalidates_signed_evidence() -> None:
    references = {
        "PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/production",
        "TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL": (
            "https://evidence.example/telephony"
        ),
    }
    environment = _signed_environment(
        references,
        runtime_overrides={
            "WEBCHAT_HUMAN_CALL_ENABLED": "true",
            "WEBCHAT_LIVE_AI_VOICE_ENABLED": "true",
            "WEBCHAT_VOICE_PROVIDER": "livekit",
            "LIVEKIT_URL": "wss://voice.example.test",
            "LIVEKIT_WEBHOOK_ENABLED": "true",
            "LIVEKIT_AGENT_NAME": "nexus-voice-agent",
            "NEXUS_VOICE_STT_MODEL": "stt-v1",
            "NEXUS_VOICE_TTS_MODEL": "tts-v1",
            "LIVEKIT_API_KEY_FILE": "/run/secrets/livekit_api_key",
            "LIVEKIT_API_SECRET_FILE": "/run/secrets/livekit_api_secret",
            "LIVEKIT_AGENT_SHARED_SECRET_FILE": (
                "/run/secrets/livekit_agent_shared_secret"
            ),
        },
    )
    environment["NEXUS_VOICE_TTS_MODEL"] = "tts-v2"
    result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(voice_enabled=True),
        identity=_identity(),
        environment=environment,
    )
    assert "activation_evidence_configuration_digest_mismatch" in result["reason_codes"]


def test_full_profile_rejects_placeholder_evidence_even_with_manifest() -> None:
    placeholder = (
        "https://github.com/Maximvonshaft/nexus_helpdesk/actions/runs/<run-id>"
    )
    result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=_signed_environment(
            {"PRODUCTION_E2E_EVIDENCE_URL": placeholder}
        ),
    )
    assert "activation_evidence_missing:production_e2e_evidence_url" in result["reason_codes"]


def test_finalizer_is_the_only_authorization_boundary() -> None:
    collected = {
        "schema": "nexus.release-readiness.v2",
        "profile": "full",
        "status": "ready",
        "reason_codes": [],
        "collectors": {
            "identity": _identity(),
            "configuration": _configuration(webchat_ai_enabled=True),
            "telephony": {
                "status": "ready",
                "enabled": False,
                "reason_codes": [],
            },
        },
        "production_authorized": False,
        "provider_enablement_authorized": False,
        "webchat_ai_enablement_authorized": False,
        "voice_enablement_authorized": False,
        "outbound_enablement_authorized": False,
        "operations_enablement_authorized": False,
    }
    blocked = finalize_release_readiness(collected, environment={})
    assert blocked["status"] == "not_ready"
    assert blocked["production_authorized"] is False
    assert blocked["webchat_ai_enablement_authorized"] is False

    references = {
        "PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/production",
        "WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL": (
            "https://evidence.example/webchat-ai"
        ),
    }
    environment = _signed_environment(
        references,
        runtime_overrides={
            "WEBCHAT_AI_ENABLED": "true",
            "WEBCHAT_AI_AUTO_REPLY_MODE": "runtime",
        },
    )
    allowed = finalize_release_readiness(
        collected,
        environment=environment,
    )
    assert allowed["status"] == "ready"
    assert allowed["production_authorized"] is True
    assert allowed["provider_enablement_authorized"] is True
    assert allowed["webchat_ai_enablement_authorized"] is True
