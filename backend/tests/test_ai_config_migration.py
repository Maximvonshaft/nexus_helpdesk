from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260502_0014_ai_config_resources.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("ai_config_resources_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ai_config_migration_revision_contract():
    module = _load_migration_module()

    assert module.revision == "20260502_0014"
    assert module.down_revision == "20260501_0013"
    assert callable(module.upgrade)


def test_ai_config_migration_declares_required_tables_and_columns():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for table_name in ["ai_config_resources", "ai_config_versions"]:
        assert f'"{table_name}"' in source

    for column_name in [
        "resource_key",
        "config_type",
        "name",
        "scope_type",
        "scope_value",
        "market_id",
        "is_active",
        "draft_summary",
        "draft_content_json",
        "published_summary",
        "published_content_json",
        "published_version",
        "published_at",
        "created_by",
        "updated_by",
        "published_by",
        "created_at",
        "updated_at",
        "resource_id",
        "version",
        "snapshot_json",
        "summary",
        "notes",
    ]:
        assert f'"{column_name}"' in source

    assert 'sa.ForeignKey("markets.id")' in source
    assert 'sa.ForeignKey("users.id")' in source
    assert 'sa.ForeignKey("ai_config_resources.id")' in source
    assert 'sa.UniqueConstraint("resource_id", "version", name="uq_ai_config_resource_version")' in source


def test_ai_config_migration_declares_required_indexes():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for index_name in [
        "ix_ai_config_resources_resource_key",
        "ix_ai_config_resources_config_type",
        "ix_ai_config_resources_name",
        "ix_ai_config_resources_scope_type",
        "ix_ai_config_resources_scope_value",
        "ix_ai_config_resources_market_id",
        "ix_ai_config_resources_is_active",
        "ix_ai_config_resources_published_at",
        "ix_ai_config_resources_created_by",
        "ix_ai_config_resources_updated_by",
        "ix_ai_config_resources_published_by",
        "ix_ai_config_resources_created_at",
        "ix_ai_config_resources_updated_at",
        "ix_ai_config_versions_resource_id",
        "ix_ai_config_versions_version",
        "ix_ai_config_versions_published_by",
        "ix_ai_config_versions_published_at",
    ]:
        assert index_name in source


def test_existing_ai_config_tests_do_not_substitute_for_migration_coverage():
    next_phase_test = Path(__file__).resolve().parent / "test_next_phase_max_push.py"
    source = next_phase_test.read_text(encoding="utf-8")

    assert "Base.metadata.create_all" in source
    assert "AIConfigResource" in source
    assert "ai_config_resources" not in source
