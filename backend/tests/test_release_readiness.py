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
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_LIVE_AI_VOICE_ENABLED", "false")
    monkeypatch.setenv("OPERATIONS_DISPATCH_MODE", "disabled")


def test_controlled_profile_is_ready_only_when_every_write_path_is_fail_closed(monkeypatch):
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
    assert result["outbound"] == {"enabled": False, "provider": "disabled"}
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


def test_full_profile_never_grants_production_authority(monkeypatch):
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
        lambda profile: {"status": "ready", "reason_codes": []},
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

    assert result["status"] == "ready"
    assert result["reason_codes"] == []
    assert result["production_authorized"] is False
    assert result["provider_enablement_authorized"] is False
    assert result["outbound_enablement_authorized"] is False


def test_collector_failures_are_namespaced_and_deduplicated(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "_identity_snapshot",
        lambda: {"status": "not_ready", "reason_codes": ["source_sha_invalid"]},
    )
    monkeypatch.setattr(
        readiness,
        "_migration_snapshot",
        lambda db: {"status": "not_ready", "reason_codes": ["migration_head_mismatch"]},
    )
    monkeypatch.setattr(
        readiness,
        "_configuration_snapshot",
        lambda profile: {"status": "not_ready", "reason_codes": ["controlled_outbound_enabled"]},
    )
    monkeypatch.setattr(
        readiness,
        "collect_queue_health",
        lambda db: {"status": "not_ready", "reason_codes": ["outbound_stale_processing"]},
    )
    monkeypatch.setattr(
        readiness,
        "check_storage_readiness",
        lambda: SimpleNamespace(as_dict=lambda: {"status": "error"}),
    )
    monkeypatch.setattr(readiness, "database_pool_snapshot", lambda: {})

    result = readiness.evaluate_release_readiness(object(), profile="controlled")

    assert result["status"] == "not_ready"
    assert result["reason_codes"] == sorted(
        {
            "identity:source_sha_invalid",
            "migration:migration_head_mismatch",
            "configuration:controlled_outbound_enabled",
            "queue:outbound_stale_processing",
            "storage:not_ready",
        }
    )
