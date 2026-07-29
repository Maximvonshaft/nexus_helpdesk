from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from app.services.activation_evidence_policy import (
    activation_evidence_snapshot,
    finalize_release_readiness,
)

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/maximvonshaft/nexus_helpdesk@{IMAGE_DIGEST}"
CONFIGURATION_DIGEST = "sha256:" + "c" * 64
ENVIRONMENT_ID = "production-eu-1"
SIGNING_KEY = "activation-evidence-test-signing-key-0123456789abcdef"


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
        "webchat_ai_enabled": False,
        "voice_enabled": False,
        "outbound": {"enabled": False, "provider": "disabled"},
        "whatsapp": {"enabled": False, "media_enabled": False, "media_scanner": "disabled"},
        "operations_mode": "disabled",
    }
    result.update(overrides)
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_environment(
    references: dict[str, str],
    **overrides: str,
) -> dict[str, str]:
    candidate = {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
        "configuration_digest": CONFIGURATION_DIGEST,
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
            "configuration_digest": CONFIGURATION_DIGEST,
            "environment_id": ENVIRONMENT_ID,
            "generated_at": generated_at,
        }
        for key, url in references.items()
    }
    unsigned = {
        "schema": "nexus.activation-evidence.v2",
        "candidate": candidate,
        "evidence": evidence,
    }
    signature = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        _canonical(unsigned),
        hashlib.sha256,
    ).hexdigest()
    manifest = {
        **unsigned,
        "signature": {
            "algorithm": "hmac-sha256",
            "value": signature,
        },
    }
    result = {
        "APP_ENV": "test",
        "ACTIVATION_EVIDENCE_SOURCE_SHA": SOURCE_SHA,
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST": IMAGE_DIGEST,
        "ACTIVATION_EVIDENCE_CONFIGURATION_DIGEST": CONFIGURATION_DIGEST,
        "ACTIVATION_EVIDENCE_ENVIRONMENT_ID": ENVIRONMENT_ID,
        "ACTIVATION_EVIDENCE_SIGNING_KEY": SIGNING_KEY,
        "ACTIVATION_EVIDENCE_MANIFEST_JSON": json.dumps(manifest),
        **references,
    }
    result.update(overrides)
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


def test_provider_canary_rejects_url_without_signed_manifest() -> None:
    result = activation_evidence_snapshot(
        profile="provider_canary",
        configuration=_configuration(),
        identity=_identity(),
        environment={
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
    result = activation_evidence_snapshot(
        profile="provider_canary",
        configuration=_configuration(),
        identity=_identity(),
        environment=_signed_environment(reference),
    )

    assert result["status"] == "ready"
    assert result["required"] == ["provider_canary_e2e_evidence_url"]
    assert result["candidate"] == {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
        "runtime_source_sha": SOURCE_SHA,
        "runtime_image_digest": IMAGE_DIGEST,
        "configuration_digest": CONFIGURATION_DIGEST,
        "environment_id": ENVIRONMENT_ID,
    }
    assert result["manifest_sha256"].startswith("sha256:")
    assert result["receipts"]["provider_canary_e2e_evidence_url"]["result"] == "pass"


def test_signed_manifest_rejects_tampering_and_wrong_candidate() -> None:
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

    wrong_candidate = _signed_environment(
        references,
        ACTIVATION_EVIDENCE_SOURCE_SHA="d" * 40,
    )
    result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=wrong_candidate,
    )
    assert "activation_evidence_source_sha_mismatch" in result["reason_codes"]


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
    allowed = finalize_release_readiness(
        collected,
        environment=_signed_environment(references),
    )
    assert allowed["status"] == "ready"
    assert allowed["production_authorized"] is True
    assert allowed["provider_enablement_authorized"] is True
    assert allowed["webchat_ai_enablement_authorized"] is True
