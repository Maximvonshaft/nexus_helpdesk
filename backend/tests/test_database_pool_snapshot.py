from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.engine import make_url

import app.db as db_module


class _FakePool:
    def checkedout(self):
        return 4

    def checkedin(self):
        return 3

    def overflow(self):
        return 1


def test_database_pool_snapshot_reports_utilization_without_connection_data(monkeypatch):
    monkeypatch.setattr(db_module, "engine", SimpleNamespace(pool=_FakePool()))
    monkeypatch.setattr(
        db_module,
        "db_url",
        make_url("postgresql+psycopg://user:password@private-host:5432/nexus"),
    )
    monkeypatch.setenv("NEXUS_PROCESS_ROLE", "worker-background")
    monkeypatch.setenv("DB_POOL_SIZE", "5")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "5")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "1800")

    result = db_module.database_pool_snapshot()

    assert result["schema"] == "nexus.database-pool-snapshot.v1"
    assert result["dialect"] == "postgresql"
    assert result["pool_class"] == "_FakePool"
    assert result["checked_out"] == 4
    assert result["checked_in"] == 3
    assert result["overflow"] == 1
    assert result["utilization_percent"] == 40.0
    assert result["configuration"]["process_role"] == "worker-background"
    assert result["configuration"]["max_connections_per_process"] == 10
    assert result["contains_connection_url"] is False
    rendered = str(result)
    assert "password" not in rendered
    assert "private-host" not in rendered
    assert "user:" not in rendered
