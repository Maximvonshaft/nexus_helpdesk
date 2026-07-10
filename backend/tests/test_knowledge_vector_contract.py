from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql, sqlite

from app.models_control_plane import KnowledgeChunk
from app.services.knowledge_runtime_v2.vector_contract import postgres_vector_type


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20260601_0047_knowledge_runtime_pg_hybrid.py"
    spec = importlib.util.spec_from_file_location("knowledge_runtime_pg_hybrid_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_postgresql_vector_type_and_sqlite_fallback_are_consistent() -> None:
    column_type = KnowledgeChunk.__table__.c.embedding_vector.type
    assert str(column_type.compile(dialect=postgresql.dialect())) == postgres_vector_type()
    assert str(column_type.compile(dialect=sqlite.dialect())).upper() == "TEXT"


def test_existing_migration_upgrade_and_downgrade_keep_vector_contract(monkeypatch) -> None:
    migration = _load_migration()
    executed = []
    dropped = []
    fake_op = SimpleNamespace(
        execute=lambda statement: executed.append(str(statement)),
        add_column=lambda *args, **kwargs: None,
        create_index=lambda *args, **kwargs: None,
        drop_index=lambda name, **kwargs: dropped.append(("index", name)),
        drop_column=lambda table, name: dropped.append((table, name)),
    )
    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration, "_is_postgres", lambda: True)

    migration.upgrade()
    migration.downgrade()

    sql = "\n".join(executed)
    assert f"embedding_vector {postgres_vector_type()}" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert ("knowledge_chunks", "embedding_vector") in dropped
    assert ("knowledge_chunks", "search_tsvector") in dropped
