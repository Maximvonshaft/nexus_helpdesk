from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260710_0056_operations_dispatch_outbox.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("operations_dispatch_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operations_dispatch_migration_upgrade_and_downgrade(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}", future=True)
    metadata = sa.MetaData()
    sa.Table("tickets", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("whatsapp_routing_rules", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    metadata.create_all(engine)

    migration = _load_migration()
    assert migration.revision == "20260710_0056"
    assert migration.down_revision == "20260709_0054"

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()  # idempotent guard

        inspector = sa.inspect(connection)
        assert "operations_dispatch_outbox" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("operations_dispatch_outbox")}
        required = {
            "id",
            "dispatch_key",
            "tenant_key",
            "country_code",
            "channel_key",
            "routing_rule_id",
            "destination_group_key",
            "destination_group_hash",
            "status",
            "attempt_count",
            "next_retry_at",
            "lease_owner",
            "lease_expires_at",
            "provider_acknowledgement",
            "external_reference_safe",
            "error_category",
            "error_summary_redacted",
            "created_at",
            "updated_at",
            "dispatched_at",
            "cancelled_at",
        }
        assert required <= columns
        unique_names = {item["name"] for item in inspector.get_unique_constraints("operations_dispatch_outbox")}
        assert "uq_operations_dispatch_outbox_dispatch_key" in unique_names
        check_names = {item["name"] for item in inspector.get_check_constraints("operations_dispatch_outbox")}
        assert {
            "ck_operations_dispatch_outbox_status",
            "ck_operations_dispatch_outbox_attempt_count_nonnegative",
            "ck_operations_dispatch_outbox_max_attempts_positive",
            "ck_operations_dispatch_outbox_lease_state",
            "ck_operations_dispatch_outbox_retry_timestamp",
            "ck_operations_dispatch_outbox_dispatched_timestamp",
            "ck_operations_dispatch_outbox_cancelled_timestamp",
        } <= check_names
        index_names = {item["name"] for item in inspector.get_indexes("operations_dispatch_outbox")}
        assert {
            "ix_operations_dispatch_outbox_scope",
            "ix_operations_dispatch_outbox_due",
            "ix_operations_dispatch_outbox_lease",
        } <= index_names

        migration.downgrade()
        migration.downgrade()  # idempotent guard
        assert "operations_dispatch_outbox" not in sa.inspect(connection).get_table_names()

    engine.dispose()
