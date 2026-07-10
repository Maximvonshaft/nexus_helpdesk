from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.knowledge_runtime_v2.vector_contract import (
    KNOWLEDGE_VECTOR_DIMENSION,
    embedding_is_current,
    validate_embedding_dimension,
    validate_embedding_vector,
)


def _load_backfill_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_knowledge_embeddings.py"
    spec = importlib.util.spec_from_file_location("knowledge_embedding_backfill_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, rows, dialect="sqlite"):
        self.rows = rows
        self.dialect = dialect
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, _model):
        return _Query(self.rows)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, statement, params):
        self.executed.append((str(statement), params))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Provider:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = 0

    def embed_texts(self, texts):
        self.calls += 1
        return self.vectors[: len(texts)]


def _row(**overrides):
    row = SimpleNamespace(
        id=1,
        normalized_text="safe policy text",
        chunk_text="safe policy text",
        semantic_hash=None,
        embedding=None,
        embedding_vector=None,
        embedding_model=None,
        embedding_dim=None,
        embedding_status="pending",
        embedding_error=None,
        embedded_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _settings():
    return SimpleNamespace(
        knowledge_embedding_dim=KNOWLEDGE_VECTOR_DIMENSION,
        knowledge_embedding_model="model-a",
        knowledge_embedding_batch_size=32,
    )


def test_vector_contract_rejects_null_wrong_dimension_and_non_finite_values() -> None:
    with pytest.raises(ValueError):
        validate_embedding_dimension(1536)
    with pytest.raises(ValueError):
        validate_embedding_vector(None)
    with pytest.raises(ValueError):
        validate_embedding_vector([0.0] * 10)
    with pytest.raises(ValueError):
        validate_embedding_vector([float("nan")] * KNOWLEDGE_VECTOR_DIMENSION)


def test_embedding_current_requires_hash_model_dimension_status_and_valid_vector() -> None:
    vector = [0.0] * KNOWLEDGE_VECTOR_DIMENSION
    row = _row(
        semantic_hash="hash-a",
        embedding=vector,
        embedding_model="model-a",
        embedding_dim=KNOWLEDGE_VECTOR_DIMENSION,
        embedding_status="embedded",
    )
    assert embedding_is_current(row, semantic_hash="hash-a", model="model-a") is True
    row.embedding_dim = 1536
    assert embedding_is_current(row, semantic_hash="hash-a", model="model-a") is False


def test_backfill_rerun_skips_valid_embedding(monkeypatch) -> None:
    module = _load_backfill_module()
    vector = [0.0] * KNOWLEDGE_VECTOR_DIMENSION
    row = _row(
        semantic_hash="hash-a",
        embedding=vector,
        embedding_model="model-a",
        embedding_dim=KNOWLEDGE_VECTOR_DIMENSION,
        embedding_status="embedded",
    )
    monkeypatch.setattr(module, "semantic_hash", lambda _text: "hash-a")
    provider = _Provider([vector])

    report = module.run_backfill(db=_DB([row]), provider=provider, settings=_settings())

    assert report["skipped"] == 1
    assert report["embedded"] == 0
    assert provider.calls == 0


def test_backfill_retries_null_embedding_and_uses_single_postgres_cast(monkeypatch) -> None:
    module = _load_backfill_module()
    vector = [0.1] * KNOWLEDGE_VECTOR_DIMENSION
    row = _row()
    monkeypatch.setattr(module, "semantic_hash", lambda _text: "hash-a")
    db = _DB([row], dialect="postgresql")

    report = module.run_backfill(
        db=db,
        provider=_Provider([vector]),
        settings=_settings(),
    )

    assert report["ok"] is True
    assert report["embedded"] == 1
    assert row.embedding == vector
    assert row.embedding_vector is None
    assert row.embedding_dim == KNOWLEDGE_VECTOR_DIMENSION
    assert len(db.executed) == 1
    assert "CAST(:vector AS vector)" in db.executed[0][0]


def test_backfill_marks_provider_cardinality_mismatch_failed(monkeypatch) -> None:
    module = _load_backfill_module()
    rows = [_row(id=1), _row(id=2)]
    monkeypatch.setattr(module, "semantic_hash", lambda text: f"hash-{text}")
    provider = _Provider([[0.0] * KNOWLEDGE_VECTOR_DIMENSION])

    report = module.run_backfill(db=_DB(rows), provider=provider, settings=_settings())

    assert report["ok"] is False
    assert report["failed"] == 2
    assert all(row.embedding_status == "failed" for row in rows)
