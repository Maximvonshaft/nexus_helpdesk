from __future__ import annotations

from app.services.nexus_osr.release_profiles import (
    CapabilityEvidence,
    CapabilityMode,
    CapabilityStatus,
    ReleaseProfileName,
    evaluate_release_profile,
    get_release_profile,
    safe_configuration_hash,
)


def _ready(reason: str = "capability.ready") -> CapabilityEvidence:
    return CapabilityEvidence(status=CapabilityStatus.READY, reason=reason)


def _full_ready(profile_name: ReleaseProfileName):
    profile = get_release_profile(profile_name)
    return profile, {name: _ready(f"{name}.ready") for name in profile.capabilities}


def test_profiles_are_versioned_and_full_osr_requires_every_capability() -> None:
    profile = get_release_profile("full_osr")
    payload = profile.as_dict()

    assert payload["schema_version"] == "nexus_osr_release_profile_v1"
    assert payload["name"] == "full_osr"
    assert payload["version"] == 1
    assert payload["capabilities"]
    assert set(payload["capabilities"].values()) == {CapabilityMode.REQUIRED.value}


def test_shadow_forbids_external_writes_and_requires_governed_read_path() -> None:
    profile = get_release_profile(ReleaseProfileName.SHADOW)

    assert profile.capabilities["external_writes"] == CapabilityMode.FORBIDDEN
    for capability in (
        "tenant_binding",
        "tracking_truth",
        "knowledge_runtime",
        "configured_escalation",
        "workers",
        "background_queue",
        "observability",
    ):
        assert profile.capabilities[capability] == CapabilityMode.REQUIRED


def test_required_missing_capability_fails_closed() -> None:
    profile, evidence = _full_ready(ReleaseProfileName.FULL_OSR)
    evidence.pop("configured_escalation")

    result = evaluate_release_profile(profile, evidence)

    assert result.status == CapabilityStatus.NOT_READY
    assert result.ready is False
    assert "configured_escalation.not_reported" in result.reasons
    assert result.capabilities["configured_escalation"]["status"] == "not_configured"


def test_required_degraded_is_degraded_not_ready_green() -> None:
    profile, evidence = _full_ready(ReleaseProfileName.SHADOW)
    evidence["workers"] = CapabilityEvidence(
        status=CapabilityStatus.DEGRADED,
        reason="workers.heartbeat_late",
        details={"oldest_seconds": 47},
    )

    result = evaluate_release_profile(profile, evidence)

    assert result.status == CapabilityStatus.DEGRADED
    assert result.ready is False
    assert result.reasons == ("workers.heartbeat_late",)


def test_forbidden_capability_enabled_blocks_profile() -> None:
    profile, evidence = _full_ready(ReleaseProfileName.SHADOW)
    evidence["external_writes"] = _ready("external_writes.enabled")

    result = evaluate_release_profile(profile, evidence)

    assert result.status == CapabilityStatus.NOT_READY
    assert "external_writes.forbidden_but_enabled" in result.reasons


def test_optional_missing_does_not_block_but_optional_failure_degrades() -> None:
    profile = get_release_profile(ReleaseProfileName.DEVELOPMENT)
    evidence = {"database": _ready("database.ready")}

    missing = evaluate_release_profile(profile, evidence)
    assert missing.status == CapabilityStatus.READY

    evidence["knowledge_runtime"] = CapabilityEvidence(
        status=CapabilityStatus.NOT_READY,
        reason="knowledge_runtime.failed",
    )
    failed = evaluate_release_profile(profile, evidence)
    assert failed.status == CapabilityStatus.DEGRADED
    assert "knowledge_runtime.failed" in failed.reasons


def test_safe_configuration_hash_is_deterministic_and_redacts_secret_fields() -> None:
    one = safe_configuration_hash(
        {
            "profile": "shadow",
            "database": True,
            "api_token": "must-not-affect-output-as-plain-text",
            "nested": {"secret_key": "hidden", "enabled": True},
        }
    )
    two = safe_configuration_hash(
        {
            "nested": {"enabled": True, "secret_key": "different-secret"},
            "api_token": "different-token",
            "database": True,
            "profile": "shadow",
        }
    )

    assert one == two
    assert one.startswith("sha256:")
    assert len(one) == 71


def test_unknown_profile_and_unsafe_reason_fail_closed() -> None:
    try:
        get_release_profile("production-ish")
    except ValueError as exc:
        assert str(exc) == "release_profile_unknown"
    else:
        raise AssertionError("unknown profile must fail closed")

    profile = get_release_profile(ReleaseProfileName.DEVELOPMENT)
    result = evaluate_release_profile(
        profile,
        {"database": CapabilityEvidence(status=CapabilityStatus.NOT_READY, reason="Bearer raw-secret-value")},
    )
    assert result.reasons == ("database.unknown",)
