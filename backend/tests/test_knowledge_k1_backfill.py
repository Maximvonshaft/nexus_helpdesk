from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.knowledge_runtime.embeddings import semantic_hash
from app.services.knowledge_runtime.runtime import KnowledgeVectorContractError
from scripts.backfill_knowledge_embeddings import run_backfill


class FakeQuery:
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


class FakeDB:
    def __init__(self, rows, dialect="sqlite"):
        self.rows = rows
        self.dialect = dialect
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, *_args):
        return FakeQuery(self.rows)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, statement, params):
        self.executed.append((str(statement), params))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeProvider:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        return self.vectors


def _settings(**overrides):
    values = {
        "knowledge_embedding_dim": 1024,
        "knowledge_embedding_batch_size": 16,
        "knowledge_embedding_model": "contract-1024",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _row(row_id=1, text="safe knowledge"):
    return SimpleNamespace(
        id=row_id,
        status="active",
        normalized_text=text,
        chunk_text=text,
        semantic_hash=None,
        embedding_model=None,
        embedding_dim=None,
        embedding_status="pending",
        embedding_error=None,
        embedding=None,
        embedding_vector=None,
        embedded_at=None,
    )


def test_sqlite_backfill_is_idempotent_and_uses_text_fallback():
    row = _row()
    provider = FakeProvider([[0.25] * 1024])
    db = FakeDB([row], dialect="sqlite")

    first = run_backfill(db, provider, _settings())
    second = run_backfill(db, provider, _settings())

    assert first == {
        "ok": True,
        "processed": 1,
        "embedded": 1,
        "skipped": 0,
        "failed": 0,
        "dry_run": False,
        "dimension": 1024,
        "storage": "text_fallback",
    }
    assert second["embedded"] == 0
    assert second["skipped"] == 1
    assert len(provider.calls) == 1
    assert row.embedding == [0.25] * 1024
    assert row.embedding_vector.startswith("[")
    assert row.semantic_hash == semantic_hash("safe knowledge")


def test_null_vector_is_retried_even_when_metadata_claims_embedded():
    row = _row()
    row.semantic_hash = semantic_hash(row.normalized_text)
    row.embedding_model = "contract-1024"
    row.embedding_dim = 1024
    row.embedding_status = "embedded"
    row.embedding = [0.1] * 1024
    row.embedding_vector = None
    provider = FakeProvider([[0.2] * 1024])

    result = run_backfill(FakeDB([row]), provider, _settings())

    assert result["embedded"] == 1
    assert provider.calls == [[row.normalized_text]]
    assert row.embedding == [0.2] * 1024


def test_provider_cardinality_mismatch_marks_entire_batch_failed():
    rows = [_row(1, "first"), _row(2, "second")]
    provider = FakeProvider([[0.1] * 1024])

    result = run_backfill(FakeDB(rows), provider, _settings())

    assert result["failed"] == 2
    assert result["embedded"] == 0
    assert all(row.embedding_status == "failed" for row in rows)
    assert all(row.embedding_error == "knowledge_embedding_cardinality_mismatch" for row in rows)


@pytest.mark.parametrize(
    "vector",
    [
        [0.1] * 1023,
        [0.1] * 1023 + [float("nan")],
        [0.1] * 1023 + [float("inf")],
    ],
)
def test_invalid_provider_vector_never_persists(vector):
    row = _row()
    result = run_backfill(FakeDB([row]), FakeProvider([vector]), _settings())

    assert result["failed"] == 1
    assert row.embedding is None
    assert row.embedding_vector is None
    assert row.embedding_status == "failed"


def test_postgres_uses_single_explicit_type_safe_vector_write():
    row = _row()
    db = FakeDB([row], dialect="postgresql")

    result = run_backfill(db, FakeProvider([[0.5] * 1024]), _settings())

    assert result["storage"] == "pgvector"
    assert row.embedding == [0.5] * 1024
    assert row.embedding_vector is None
    assert len(db.executed) == 1
    sql, params = db.executed[0]
    assert "CAST(:vector AS vector(1024))" in sql
    assert params["id"] == 1
    assert params["vector"].startswith("[")


def test_backfill_rejects_invalid_configured_dimension_before_provider_call():
    row = _row()
    provider = FakeProvider([[0.1] * 1024])

    with pytest.raises(KnowledgeVectorContractError, match="knowledge_vector_dimension_mismatch"):
        run_backfill(
            FakeDB([row]),
            provider,
            _settings(knowledge_embedding_dim=1536),
        )

    assert provider.calls == []
