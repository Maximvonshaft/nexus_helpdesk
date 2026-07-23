from __future__ import annotations

import app.services.release_readiness as readiness

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/maximvonshaft/nexus_helpdesk@{IMAGE_DIGEST}"


def _full_configuration() -> dict[str, object]:
    return {
        "webchat_ai_enabled": False,
        "voice_enabled": False,
        "outbound": {"enabled": False, "provider": "disabled"},
        "operations_mode": "disabled",
    }


def _bind_candidate(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SHA", SOURCE_SHA)
    monkeypatch.setenv("IMAGE_TAG", IMAGE)
    monkeypatch.setenv("ACTIVATION_EVIDENCE_SOURCE_SHA", SOURCE_SHA)
    monkeypatch.setenv("ACTIVATION_EVIDENCE_IMAGE_DIGEST", IMAGE_DIGEST)
    monkeypatch.setenv(
        "PRODUCTION_E2E_EVIDENCE_URL",
        "https://evidence.example/production",
    )


def test_controlled_profile_never_requires_external_activation_evidence(
    monkeypatch,
) -> None:
    for key in (
        "ACTIVATION_EVIDENCE_SOURCE_SHA",
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST",
        "PRODUCTION_E2E_EVIDENCE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    result = readiness._activation_evidence_snapshot(
        "controlled",
        _full_configuration(),
    )

    assert result["status"] == "ready"
    assert result["required"] == []
    assert result["candidate"] is None
    assert result["reason_codes"] == []


def test_full_profile_binds_evidence_to_runtime_sha_and_image(monkeypatch) -> None:
    _bind_candidate(monkeypatch)

    result = readiness._activation_evidence_snapshot(
        "full",
        _full_configuration(),
    )

    assert result["status"] == "ready"
    assert result["reason_codes"] == []
    assert result["candidate"] == {
        "source_sha": SOURCE_SHA,
        "image_digest": IMAGE_DIGEST,
        "runtime_source_sha": SOURCE_SHA,
        "runtime_image_digest": IMAGE_DIGEST,
    }


def test_full_profile_rejects_placeholder_or_wrong_candidate_evidence(
    monkeypatch,
) -> None:
    _bind_candidate(monkeypatch)
    monkeypatch.setenv(
        "PRODUCTION_E2E_EVIDENCE_URL",
        "https://github.com/Maximvonshaft/nexus_helpdesk/actions/runs/<run-id>",
    )
    result = readiness._activation_evidence_snapshot(
        "full",
        _full_configuration(),
    )
    assert (
        "activation_evidence_invalid:production_e2e_evidence_url"
        in result["reason_codes"]
    )

    _bind_candidate(monkeypatch)
    monkeypatch.setenv("ACTIVATION_EVIDENCE_SOURCE_SHA", "c" * 40)
    result = readiness._activation_evidence_snapshot(
        "full",
        _full_configuration(),
    )
    assert "activation_evidence_source_sha_mismatch" in result["reason_codes"]

    _bind_candidate(monkeypatch)
    monkeypatch.setenv(
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST",
        "sha256:" + "d" * 64,
    )
    result = readiness._activation_evidence_snapshot(
        "full",
        _full_configuration(),
    )
    assert "activation_evidence_image_digest_mismatch" in result["reason_codes"]
