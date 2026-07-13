from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.model_registry import register_all_models
from app.models import Tenant
from app.db import Base


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260713_0059_add_tenant_principal.py"
)
CORE_TABLES = (
    "markets",
    "teams",
    "users",
    "channel_accounts",
    "customers",
    "tickets",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_principal_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_additive_schema(connection) -> None:
    inspector = sa.inspect(connection)
    assert "tenants" in inspector.get_table_names()

    tenant_columns = {item["name"]: item for item in inspector.get_columns("tenants")}
    assert set(tenant_columns) == {
        "id",
        "tenant_key",
        "display_name",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert tenant_columns["tenant_key"]["nullable"] is False
    assert tenant_columns["display_name"]["nullable"] is False
    assert {item["name"] for item in inspector.get_unique_constraints("tenants")} >= {
        "uq_tenants_tenant_key"
    }
    assert {item["name"] for item in inspector.get_check_constraints("tenants")} >= {
        "ck_tenants_key_nonempty",
        "ck_tenants_key_lowercase",
        "ck_tenants_key_not_default",
    }
    assert "ix_tenants_is_active" in {
        item["name"] for item in inspector.get_indexes("tenants")
    }

    for table_name in CORE_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table_name)}
        assert columns["tenant_id"]["nullable"] is True
        assert columns["tenant_assignment_source"]["nullable"] is True
        assert columns["tenant_assignment_version"]["nullable"] is True
        assert f"ix_{table_name}_tenant_id" in {
            item["name"] for item in inspector.get_indexes(table_name)
        }
        foreign_keys = {
            item["name"]: item for item in inspector.get_foreign_keys(table_name)
        }
        fk = foreign_keys[f"fk_{table_name}_tenant_id_tenants"]
        assert fk["referred_table"] == "tenants"
        assert fk["constrained_columns"] == ["tenant_id"]
        assert fk["referred_columns"] == ["id"]


def test_model_metadata_exposes_nullable_relational_tenant_principal() -> None:
    register_all_models()
    assert Tenant.__table__.name == "tenants"
    assert Tenant.__table__.c.tenant_key.nullable is False
    assert Tenant.__table__.c.tenant_key.default is None
    assert {
        constraint.name for constraint in Tenant.__table__.constraints
    } >= {
        "uq_tenants_tenant_key",
        "ck_tenants_key_nonempty",
        "ck_tenants_key_lowercase",
        "ck_tenants_key_not_default",
    }

    for table_name in CORE_TABLES:
        table = Base.metadata.tables[table_name]
        tenant_id = table.c.tenant_id
        assert tenant_id.nullable is True
        assert tenant_id.default is None
        assert tenant_id.server_default is None
        assert {fk.target_fullname for fk in tenant_id.foreign_keys} == {"tenants.id"}
        assert table.c.tenant_assignment_source.nullable is True
        assert table.c.tenant_assignment_version.nullable is True


def test_additive_migration_preserves_rows_across_downgrade_and_reupgrade(tmp_path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'tenant-phase1.db'}", future=True)
    metadata = sa.MetaData()
    for table_name in CORE_TABLES:
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("marker", sa.String(40), nullable=False),
        )
    metadata.create_all(engine)

    migration = _load_migration()
    assert migration.revision == "20260713_0059"
    assert migration.down_revision == "20260711_0058"

    with engine.begin() as connection:
        for table_name in CORE_TABLES:
            connection.execute(
                sa.text(f"INSERT INTO {table_name} (id, marker) VALUES (1, :marker)"),
                {"marker": f"preserve-{table_name}"},
            )

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        _assert_additive_schema(connection)

        assert connection.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() == 0
        for table_name in CORE_TABLES:
            row = connection.execute(
                sa.text(
                    f"SELECT marker, tenant_id, tenant_assignment_source, "
                    f"tenant_assignment_version FROM {table_name} WHERE id = 1"
                )
            ).one()
            assert row.marker == f"preserve-{table_name}"
            assert row.tenant_id is None
            assert row.tenant_assignment_source is None
            assert row.tenant_assignment_version is None

        connection.execute(
            sa.text(
                "INSERT INTO tenants (id, tenant_key, display_name, is_active) "
                "VALUES (1, 'tenant-a', 'Tenant A', 1)"
            )
        )
        for table_name in CORE_TABLES:
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET tenant_id = 1, "
                    "tenant_assignment_source = 'mapping_manifest', "
                    "tenant_assignment_version = 'sha256:test' WHERE id = 1"
                )
            )

        migration.downgrade()
        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "tenants" not in inspector.get_table_names()
        for table_name in CORE_TABLES:
            columns = {item["name"] for item in inspector.get_columns(table_name)}
            assert not {
                "tenant_id",
                "tenant_assignment_source",
                "tenant_assignment_version",
            } & columns
            marker = connection.execute(
                sa.text(f"SELECT marker FROM {table_name} WHERE id = 1")
            ).scalar_one()
            assert marker == f"preserve-{table_name}"

        migration.upgrade()
        _assert_additive_schema(connection)
        assert connection.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() == 0
        for table_name in CORE_TABLES:
            row = connection.execute(
                sa.text(f"SELECT marker, tenant_id FROM {table_name} WHERE id = 1")
            ).one()
            assert row.marker == f"preserve-{table_name}"
            assert row.tenant_id is None

    engine.dispose()


def test_additive_migration_contains_no_insert_or_backfill_statement() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8").lower()
    assert "insert into tenants" not in source
    assert "update markets" not in source
    assert "update teams" not in source
    assert "update users" not in source
    assert "update channel_accounts" not in source
    assert "update customers" not in source
    assert "update tickets" not in source
    assert "server_default='default'" not in source
    assert 'server_default="default"' not in source
