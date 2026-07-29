from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260728_r6_handoff_routing.py"
SPEC = importlib.util.spec_from_file_location("r15_r6_handoff_migration", MIGRATION)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_r6_downgrade_rejects_multi_queue_data_before_destructive_ddl(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE operator_queue_scope_grants ("
                "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                "tenant_key VARCHAR(80) NOT NULL, country_code VARCHAR(16) NOT NULL, "
                "channel_key VARCHAR(40) NOT NULL, queue_key VARCHAR(160) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO operator_queue_scope_grants "
                "(id, user_id, tenant_key, country_code, channel_key, queue_key) VALUES "
                "(1, 7, 'tenant-a', 'CH', 'website', 'delivery_exceptions'), "
                "(2, 7, 'tenant-a', 'CH', 'website', 'customer_support')"
            )
        )

        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        destructive_calls: list[str] = []
        monkeypatch.setattr(
            migration.op,
            "drop_table",
            lambda name: destructive_calls.append(f"drop_table:{name}"),
        )
        monkeypatch.setattr(
            migration.op,
            "drop_index",
            lambda *args, **kwargs: destructive_calls.append("drop_index"),
        )

        with pytest.raises(
            RuntimeError,
            match="r6_handoff_routing_downgrade_irreversible_multi_queue_grants",
        ):
            migration.downgrade()
        assert destructive_calls == []
        rows = connection.execute(
            text("SELECT queue_key FROM operator_queue_scope_grants ORDER BY id")
        ).scalars().all()
        assert rows == ["delivery_exceptions", "customer_support"]

    engine.dispose()
