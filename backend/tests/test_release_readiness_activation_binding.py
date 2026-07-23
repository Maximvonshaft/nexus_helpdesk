from __future__ import annotations

from app.services.activation_evidence_policy import (
    activation_evidence_snapshot,
    finalize_release_readiness,
)

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/maximvonshaft/nexus_helpdesk@{IMAGE_DIGEST}"


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
        "operations_mode": "disabled",
    }
    result.update(overrides)
    return result


def _binding_env(**overrides: str) -> dict[str, str]:
    result = {
        "ACTIVATION_EVIDENCE_SOURCE_SHA": SOURCE_SHA,
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST": IMAGE_DIGEST,
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


def test_provider_canary_requires_bound_canary_evidence_only() -> None:
    result = activation_evidence_snapshot(
        profile="provider_canary",
        configuration=_configuration(),
        identity=_identity(),
        environment=_binding_env(
            PROVIDER_CANARY_E2E_EVIDENCE_URL=(
                "https://evidence.example/provider-canary"
            )
        ),
    )

    assert result["status"] == "ready"
    assert result["required"] == ["provider_canary_e2e_evidence_url"]
    assert result["candidate"] == {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
        "runtime_source_sha": SOURCE_SHA,
        "runtime_image_digest": IMAGE_DIGEST,
    }


def test_full_profile_rejects_placeholder_or_wrong_candidate_evidence() -> None:
    result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=_binding_env(
            PRODUCTION_E2E_EVIDENCE_URL=(
                "https://github.com/Maximvonshaft/nexus_helpdesk/actions/runs/<run-id>"
            )
        ),
    )
    assert (
        "activation_evidence_missing:production_e2e_evidence_url"
        in result["reason_codes"]
    )

    result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=_binding_env(
            ACTIVATION_EVIDENCE_SOURCE_SHA="c" * 40,
            PRODUCTION_E2E_EVIDENCE_URL="https://evidence.example/production",
        ),
    )
    assert "activation_evidence_source_sha_mismatch" in result["reason_codes"]

    result = activation_evidence_snapshot(
        profile="full",
        configuration=_configuration(),
        identity=_identity(),
        environment=_binding_env(
            ACTIVATION_EVIDENCE_IMAGE_DIGEST="sha256:" + "d" * 64,
            PRODUCTION_E2E_EVIDENCE_URL="https://evidence.example/production",
        ),
    )
    assert "activation_evidence_image_digest_mismatch" in result["reason_codes"]


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

    allowed = finalize_release_readiness(
        collected,
        environment=_binding_env(
            PRODUCTION_E2E_EVIDENCE_URL="https://evidence.example/production",
            WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL=(
                "https://evidence.example/webchat-ai"
            ),
        ),
    )
    assert allowed["status"] == "ready"
    assert allowed["production_authorized"] is True
    assert allowed["provider_enablement_authorized"] is True
    assert allowed["webchat_ai_enablement_authorized"] is True
