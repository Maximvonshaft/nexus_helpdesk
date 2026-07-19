from __future__ import annotations

from pathlib import Path

from app.models import ExternalChannelUnresolvedEvent

ROOT = Path(__file__).resolve().parents[2]


def test_external_channel_runtime_module_is_permanently_absent() -> None:
    services = ROOT / "backend/app/services"
    assert not (services / "external_channel_bridge.py").exists()
    assert not (services / "external_channel_runtime_service.py").exists()
    assert not (services / "external_channel_unresolved_store.py").exists()
    assert not (services / "external_channel_payload_hash.py").exists()


def test_historical_unresolved_event_schema_remains_readable_for_migration() -> None:
    table = ExternalChannelUnresolvedEvent.__table__
    assert "payload_hash" in table.c
    assert "payload_json" in table.c
    assert "status" in table.c
    assert any(
        index.name == "uq_external_channel_unresolved_active_payload_hash"
        for index in table.indexes
    )


def test_service_package_has_no_external_channel_monkey_patch() -> None:
    service_init = (ROOT / "backend/app/services/__init__.py").read_text(encoding="utf-8")
    assert "external_channel_unresolved_store" not in service_init
    assert "apply_external_channel_unresolved_store_patch" not in service_init
