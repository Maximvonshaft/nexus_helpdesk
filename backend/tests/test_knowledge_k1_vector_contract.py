from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from app.models_control_plane import (
    KNOWLEDGE_VECTOR_DIMENSION,
    PGVector,
    KnowledgeChunk,
)
from app.services.knowledge_runtime_v2.runtime import (
    KnowledgeVectorContractError,
    validate_embedding_batch,
    validate_embedding_dimension,
    validate_knowledge_vector,
)


ROOT = Path(__file__).resolve().parents[2]


def test_postgres_and_sqlite_vector_types_match_contract():
    pg = postgresql.dialect()
    lite = sqlite.dialect()

    assert KNOWLEDGE_VECTOR_DIMENSION == 384
    assert KnowledgeChunk.__table__.c.embedding_vector.type.dialect_impl(pg).compile(dialect=pg) == "vector(384)"
    assert KnowledgeChunk.__table__.c.embedding_vector.type.dialect_impl(lite).compile(dialect=lite).upper() == "TEXT"
    assert PGVector(384).get_col_spec() == "vector(384)"


@pytest.mark.parametrize("value", [1536, 0, -1, True, None, "bad"])
def test_invalid_configured_dimension_fails_closed(value):
    with pytest.raises(KnowledgeVectorContractError, match="knowledge_vector_dimension_mismatch"):
        validate_embedding_dimension(value)


@pytest.mark.parametrize(
    "vector",
    [
        [0.0] * 383,
        [0.0] * 385,
        [0.0] * 383 + [float("nan")],
        [0.0] * 383 + [float("inf")],
        [0.0] * 383 + [True],
        [0.0] * 383 + ["0"],
        None,
    ],
)
def test_invalid_vector_fails_closed(vector):
    with pytest.raises(KnowledgeVectorContractError, match="knowledge_embedding_vector_invalid"):
        validate_knowledge_vector(vector)


def test_provider_cardinality_must_match_input():
    with pytest.raises(KnowledgeVectorContractError, match="knowledge_embedding_cardinality_mismatch"):
        validate_embedding_batch([[0.0] * 384], expected_count=2)


def test_existing_vector_migration_matches_orm_without_new_revision():
    migration = (
        ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "20260601_0047_knowledge_runtime_pg_hybrid.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260601_0047"' in migration
    assert 'down_revision = "20260601_0046"' in migration
    assert "vector(384)" in migration
    assert 'sa.Column("embedding_vector", sa.Text(), nullable=True)' in migration
    assert 'op.drop_column("knowledge_chunks", "embedding_vector")' in migration
