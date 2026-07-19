from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260716_0062_canonical_runtime_contracts.py"


class _OpFacade:
    def __init__(self, connection: sa.Connection):
        self.connection = connection

    def get_bind(self):
        return self.connection

    def execute(self, statement):
        return self.connection.execute(statement)

    def create_table(self, name, *columns):
        table = sa.Table(name, sa.MetaData(), *columns)
        table.create(self.connection)
        return table

    def drop_table(self, name):
        table = sa.Table(name, sa.MetaData(), autoload_with=self.connection)
        table.drop(self.connection)


def _module(connection: sa.Connection):
    spec = importlib.util.spec_from_file_location("migration_0062_test", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = _OpFacade(connection)
    return module


def _rules(connection: sa.Connection) -> dict[int, str]:
    return {
        int(row.id): str(row.output_contract)
        for row in connection.execute(
            sa.text("SELECT id, output_contract FROM provider_routing_rules ORDER BY id")
        )
    }


def test_upgrade_and_downgrade_restore_only_rows_changed_by_migration() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE provider_routing_rules (
                    id INTEGER PRIMARY KEY,
                    scenario VARCHAR(160) NOT NULL,
                    output_contract VARCHAR(160) NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO provider_routing_rules (id, scenario, output_contract)
                VALUES
                  (1, 'webchat_runtime_reply', 'nexus_webchat_runtime_reply_v1'),
                  (2, 'webchat_runtime_reply', 'nexus.webchat_runtime_reply'),
                  (3, 'another_scenario', 'nexus_webchat_runtime_reply_v1')
                """
            )
        )
        migration = _module(connection)

        migration.upgrade()
        assert _rules(connection) == {
            1: "nexus.webchat_runtime_reply",
            2: "nexus.webchat_runtime_reply",
            3: "nexus_webchat_runtime_reply_v1",
        }
        provenance = connection.execute(
            sa.text("SELECT rule_id FROM migration_0062_runtime_contract_rows ORDER BY rule_id")
        ).scalars().all()
        assert provenance == [1]

        connection.execute(
            sa.text(
                """
                INSERT INTO provider_routing_rules (id, scenario, output_contract)
                VALUES (4, 'webchat_runtime_reply', 'nexus.webchat_runtime_reply')
                """
            )
        )
        migration.downgrade()

        assert _rules(connection) == {
            1: "nexus_webchat_runtime_reply_v1",
            2: "nexus.webchat_runtime_reply",
            3: "nexus_webchat_runtime_reply_v1",
            4: "nexus.webchat_runtime_reply",
        }
        assert not sa.inspect(connection).has_table("migration_0062_runtime_contract_rows")


def test_downgrade_without_provenance_fails_closed() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE provider_routing_rules (
                    id INTEGER PRIMARY KEY,
                    scenario VARCHAR(160) NOT NULL,
                    output_contract VARCHAR(160) NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
        migration = _module(connection)
        with pytest.raises(RuntimeError, match="downgrade_provenance_missing"):
            migration.downgrade()


def test_downgrade_rejects_rows_changed_after_upgrade() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE provider_routing_rules (
                    id INTEGER PRIMARY KEY,
                    scenario VARCHAR(160) NOT NULL,
                    output_contract VARCHAR(160) NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO provider_routing_rules (id, scenario, output_contract)
                VALUES (1, 'webchat_runtime_reply', 'nexus_webchat_runtime_reply_v1')
                """
            )
        )
        migration = _module(connection)
        migration.upgrade()
        connection.execute(
            sa.text(
                "UPDATE provider_routing_rules SET output_contract='other.contract' WHERE id=1"
            )
        )
        with pytest.raises(RuntimeError, match="downgrade_conflict"):
            migration.downgrade()
