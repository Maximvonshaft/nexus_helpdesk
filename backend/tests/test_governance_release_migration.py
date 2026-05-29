from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260529_0039_governance_release_queue.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("governance_release_queue_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_governance_release_migration_revision_contract():
    module = _load_migration_module()

    assert module.revision == "20260529_0039"
    assert module.down_revision == "20260527_0038"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_governance_release_migration_declares_tables_columns_indexes():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for table_name in ["governance_release_requests", "governance_release_events"]:
        assert f'"{table_name}"' in source

    for column_name in [
        "source_type",
        "source_id",
        "release_type",
        "risk_level",
        "impact_json",
        "diff_json",
        "rollback_plan",
        "audit_target_type",
        "audit_target_id",
        "requested_by",
        "approved_by",
        "published_by",
        "rolled_back_by",
        "submitted_at",
        "approved_at",
        "published_at",
        "rolled_back_at",
        "event_type",
        "payload_json",
        "request_id",
    ]:
        assert f'"{column_name}"' in source

    for index_name in [
        "ix_governance_release_status_created",
        "ix_governance_release_source",
        "ix_governance_release_risk_status",
        "ix_governance_release_events_release_created",
    ]:
        assert index_name in source

    assert 'sa.ForeignKey("users.id")' in source
    assert 'sa.ForeignKey("governance_release_requests.id")' in source
