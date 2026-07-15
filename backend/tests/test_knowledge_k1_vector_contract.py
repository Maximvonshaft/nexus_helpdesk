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

    assert KNOWLEDGE_VECTOR_DIMENSION == 1024
    assert KnowledgeChunk.__table__.c.embedding_vector.type.dialect_impl(pg).compile(dialect=pg) == "vector(1024)"
    assert KnowledgeChunk.__table__.c.embedding_vector.type.dialect_impl(lite).compile(dialect=lite).upper() == "TEXT"
    assert PGVector(1024).get_col_spec() == "vector(1024)"


@pytest.mark.parametrize("value", [1536, 0, -1, True, None, "bad"])
def test_invalid_configured_dimension_fails_closed(value):
    with pytest.raises(KnowledgeVectorContractError, match="knowledge_vector_dimension_mismatch"):
        validate_embedding_dimension(value)


@pytest.mark.parametrize(
    "vector",
    [
        [0.0] * 1023,
        [0.0] * 1025,
        [0.0] * 1023 + [float("nan")],
        [0.0] * 1023 + [float("inf")],
        [0.0] * 1023 + [True],
        [0.0] * 1023 + ["0"],
        None,
    ],
)
def test_invalid_vector_fails_closed(vector):
    with pytest.raises(KnowledgeVectorContractError, match="knowledge_embedding_vector_invalid"):
        validate_knowledge_vector(vector)


def test_provider_cardinality_must_match_input():
    with pytest.raises(KnowledgeVectorContractError, match="knowledge_embedding_cardinality_mismatch"):
        validate_embedding_batch([[0.0] * 1024], expected_count=2)


def test_vector_dimension_migration_matches_orm():
    migration = (
        ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "20260715_0061_bge_m3_vector_dimension.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260715_0061"' in migration
    assert 'down_revision = "20260715_0060"' in migration
    assert "_replace_postgres_vector_dimension(1024)" in migration
    assert "embedding_status = 'pending'" in migration
    assert "_replace_postgres_vector_dimension(384)" in migration
