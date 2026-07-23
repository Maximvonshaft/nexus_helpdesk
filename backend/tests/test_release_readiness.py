from __future__ import annotations

from types import SimpleNamespace

import app.services.release_readiness as readiness


def _controlled_env(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "control")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "0")
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "false")
    monkeypatch.setenv("OUTBOUND_PROVIDER", "disabled")
    monkeypatch.setenv("WEBCHAT_AI_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("OPERATIONS_DISPATCH_MODE", "disabled")


def _activation_binding_env(monkeypatch):
    source_sha = "a" * 40
    image_digest = "sha256:" + "b" * 64
    monkeypatch.setenv("GIT_SHA", source_sha)
    monkeypatch.setenv(
        "IMAGE_TAG",
        f"ghcr.io/maximvonshaft/nexus_helpdesk@{image_digest}",
    )
    monkeypatch.setenv("ACTIVATION_EVIDENCE_SOURCE_SHA", source_sha)
    monkeypatch.setenv("ACTIVATION_EVIDENCE_IMAGE_DIGEST", image_digest)
    return source_sha, image_digest


def test_controlled_profile_is_ready_only_when_every_write_path_is_fail_closed(
    monkeypatch,
):
    _controlled_env(monkeypatch)

    result = readiness._configuration_snapshot("controlled")

    assert result["status"] == "ready"
    assert result["reason_codes"] == []
    assert result["provider"] == {
        "enabled": False,
        "mode": "control",
        "kill_switch": True,
        "canary_percent": 0,
    }
    assert result["outbound"] == {
        "enabled": False,
        "provider": "disabled",
    }
    assert result["contains_secrets"] is False


def test_controlled_profile_rejects_provider_or_outbound_enablement(monkeypatch):
    _controlled_env(monkeypatch)
    monkeypatch.setenv("PROVIDER_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setenv("ENABLE_OUTBOUND_DISPATCH", "true")
    monkeypatch.setenv("OUTBOUND_PROVIDER", "smtp")

    result = readiness._configuration_snapshot("controlled")

    assert result["status"] == "not_ready"
    assert {
        "controlled_provider_enabled",
        "controlled_provider_kill_switch_inactive",
        "controlled_outbound_enabled",
    }.issubset(set(result["reason_codes"]))


def test_provider_canary_profile_allows_only_bounded_provider_traffic(monkeypatch):
    _controlled_env(monkeypatch)
    monkeypatch.setenv("PROVIDER_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "canary")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "5")

    result = readiness._configuration_snapshot("provider_canary")

    assert result["status"] == "ready"
    assert result["reason_codes"] == []

    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "50")
    result = readiness._configuration_snapshot("provider_canary")
    assert result["status"] == "not_ready"
    assert "canary_percent_outside_approved_range" in result["reason_codes"]


def test_full_profile_grants_authority_only_after_every_collector_passes(
    monkeypatch,
):
    monkeypatch.setattr(
        readiness,
        "_identity_snapshot",
        lambda: {"status": "ready", "reason_codes": []},
    )
    monkeypatch.setattr(
        readiness,
        "_migration_snapshot",
        lambda db: {"status": "ready", "reason_codes": []},
    )
    monkeypatch.setattr(
        readiness,
        "_configuration_snapshot",
        lambda profile: {
            "status": "ready",
            "reason_codes": [],
            "webchat_ai_enabled": False,
            "voice_enabled": False,
            "outbound": {"enabled": False, "provider": "disabled"},
            "operations_mode": "disabled",
        },
    )
    monkeypatch.setattr(
        readiness,
        "_telephony_snapshot",
        lambda db: {
            "status": "ready",
            "enabled": False,
            "reason_codes": [],
        },
    )
    monkeypatch.setattr(
        readiness,
        "_activation_evidence_snapshot",
        lambda profile, configuration: {
            "status": "ready",
            "reason_codes": [],
            "references": {
                "production_e2e_evidence_url": "https://evidence.example/full",
            },
        },
    )
    monkeypatch.setattr(
        readiness,
        "collect_queue_health",
        lambda db: {"status": "ready", "reason_codes": []},
    )
    monkeypatch.setattr(
        readiness,
        "check_storage_readiness",
        lambda: SimpleNamespace(as_dict=lambda: {"status": "ok"}),
    )
    monkeypatch.setattr(
        readiness,
        "database_pool_snapshot",
        lambda: {"schema": "nexus.database-pool-snapshot.v1"},
    )

    result = readiness.evaluate_release_readiness(object(), profile="full")

    assert result["schema"] == "nexus.release-readiness.v2"
    assert result["status"] == "ready"
    assert result["reason_codes"] == []
    assert result["production_authorized"] is True
    assert result["provider_enablement_authorized"] is True
    assert result["webchat_ai_enablement_authorized"] is False
    assert result["voice_enablement_authorized"] is False
    assert result["outbound_enablement_authorized"] is False
    assert result["operations_enablement_authorized"] is False


def test_full_profile_requires_https_activation_evidence(monkeypatch):
    _activation_binding_env(monkeypatch)
    for key in (
        "PRODUCTION_E2E_EVIDENCE_URL",
        "WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL",
        "TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL",
        "OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL",
        "OPERATIONS_PRODUCTION_E2E_EVIDENCE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    result = readiness._activation_evidence_snapshot(
        "full",
        {
            "webchat_ai_enabled": True,
            "voice_enabled": True,
            "outbound": {"enabled": True, "provider": "smtp"},
            "operations_mode": "enabled",
        },
    )

    assert result["status"] == "not_ready"
    assert len(result["reason_codes"]) == 5
    assert all(
        item.startswith("activation_evidence_missing:")
        for item in result["reason_codes"]
    )

    monkeypatch.setenv("PRODUCTION_E2E_EVIDENCE_URL", "http://unsafe.example")
    result = readiness._activation_evidence_snapshot(
        "full",
        {
            "webchat_ai_enabled": False,
            "voice_enabled": False,
            "outbound": {"enabled": False, "provider": "disabled"},
            "operations_mode": "disabled",
        },
    )
    assert result["reason_codes"] == [
        "activation_evidence_invalid:production_e2e_evidence_url"
    ]


def test_runtime_activation_evidence_rejects_candidate_mismatch(monkeypatch):
    source_sha, image_digest = _activation_binding_env(monkeypatch)
    monkeypatch.setenv("PRODUCTION_E2E_EVIDENCE_URL", "https://evidence.example/full")
    monkeypatch.setenv("ACTIVATION_EVIDENCE_SOURCE_SHA", "c" * 40)

    result = readiness._activation_evidence_snapshot(
        "full",
        {
            "webchat_ai_enabled": False,
            "voice_enabled": False,
            "outbound": {"enabled": False, "provider": "disabled"},
            "operations_mode": "disabled",
        },
    )

    assert result["status"] == "not_ready"
    assert "activation_evidence_source_sha_mismatch" in result["reason_codes"]
    assert result["candidate"]["runtime_source_sha"] == source_sha
    assert result["candidate"]["runtime_image_digest"] == image_digest


def test_collector_failures_are_namespaced_and_deduplicated(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "_identity_snapshot",
        lambda: {"status": "not_ready", "reason_codes": ["source_sha_invalid"]},
    )
    monkeypatch.setattr(
        readiness,
        "_migration_snapshot",
        lambda db: {
            "status": "not_ready",
            "reason_codes": ["migration_head_mismatch"],
        },
    )
    monkeypatch.setattr(
        readiness,
        "_configuration_snapshot",
        lambda profile: {
            "status": "not_ready",
            "reason_codes": ["controlled_outbound_enabled"],
            "webchat_ai_enabled": False,
            "voice_enabled": False,
            "outbound": {"enabled": False, "provider": "disabled"},
            "operations_mode": "disabled",
        },
    )
    monkeypatch.setattr(
        readiness,
        "_telephony_snapshot",
        lambda db: {"status": "ready", "reason_codes": [], "enabled": False},
    )
    monkeypatch.setattr(
        readiness,
        "_activation_evidence_snapshot",
        lambda profile, configuration: {"status": "ready", "reason_codes": []},
    )
    monkeypatch.setattr(
        readiness,
        "collect_queue_health",
        lambda db: {
            "status": "not_ready",
            "reason_codes": ["outbound_stale_processing"],
        },
    )
    monkeypatch.setattr(
        readiness,
        "check_storage_readiness",
        lambda: SimpleNamespace(as_dict=lambda: {"status": "error"}),
    )
    monkeypatch.setattr(readiness, "database_pool_snapshot", lambda: {})

    result = readiness.evaluate_release_readiness(object(), profile="controlled")

    assert result["status"] == "not_ready"
    assert result["production_authorized"] is False
    assert result["reason_codes"] == sorted(
        {
            "identity:source_sha_invalid",
            "migration:migration_head_mismatch",
            "configuration:controlled_outbound_enabled",
            "queue:outbound_stale_processing",
            "storage:not_ready",
        }
    )
