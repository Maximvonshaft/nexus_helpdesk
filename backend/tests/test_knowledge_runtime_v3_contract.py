from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from app.models_control_plane import KnowledgeChunk
from app.services.knowledge_runtime_v2.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    get_embedding_provider,
    vector_literal,
)
from app.services.knowledge_runtime_v2.runtime import _postgres_candidate_sql


ROOT = Path(__file__).resolve().parents[2]


def test_postgres_hybrid_sql_uses_tsvector_and_pgvector():
    fts_sql, fts_params = _postgres_candidate_sql(
        vector=False,
        tenant_id="default",
        brand_id="speedaf",
        country_scope="CH",
        channel_scope="website",
        market_id=1,
        channel="website",
        audience_scope="customer",
        language="zh",
    )
    vector_sql, vector_params = _postgres_candidate_sql(
        vector=True,
        tenant_id="default",
        brand_id="speedaf",
        country_scope="CH",
        channel_scope="website",
        market_id=1,
        channel="website",
        audience_scope="customer",
        language="zh",
    )

    assert "websearch_to_tsquery" in fts_sql
    assert "search_tsvector @@ q.query" in fts_sql
    assert "ts_rank_cd" in fts_sql
    assert "probe_category" in fts_sql
    assert "embedding_vector <=> CAST(:query_vector AS vector)" in vector_sql
    assert "kc.embedding_vector IS NOT NULL" in vector_sql
    assert "kc.tenant_id = :tenant_id" in fts_sql
    assert "kc.country_scope IN (:country_scope, :global_country_scope)" in fts_sql
    assert fts_params["market_id"] == 1
    assert fts_params["tenant_id"] == "default"
    assert fts_params["country_scope"] == "CH"
    assert vector_params["channel"] == "website"


def test_knowledge_chunk_pg_hybrid_columns_use_postgres_types():
    dialect = postgresql.dialect()

    assert KnowledgeChunk.__table__.c.search_tsvector.type.dialect_impl(dialect).compile(dialect=dialect) == "TSVECTOR"
    assert KnowledgeChunk.__table__.c.embedding_vector.type.dialect_impl(dialect).compile(dialect=dialect) == "vector(1024)"


def test_openai_compatible_embedding_provider_requests_and_parses_ordered_vectors(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                        {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="secret",
        model="text-embedding-3-small",
        dim=3,
        timeout_seconds=7,
    )

    vectors = provider.embed_texts(["first", "second"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert captured == {
        "url": "https://embedding.example/v1/embeddings",
        "timeout": 7,
        "authorization": "Bearer secret",
        "body": {
            "model": "text-embedding-3-small",
            "input": ["first", "second"],
            "dimensions": 3,
        },
    }
    assert vector_literal([1, 0.25, -0.5]) == "[1.00000000,0.25000000,-0.50000000]"


def test_openai_compatible_embedding_provider_fails_closed_without_dimension_capability():
    with pytest.raises(ValueError, match="embedding_provider_dimension_request_unsupported"):
        OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="secret",
            model="fixed-native-model",
            dim=1024,
            timeout_seconds=7,
            dimension_request_supported=False,
        )


def test_embedding_provider_factory_fails_closed_when_dimension_requests_are_unsupported(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_DIMENSION_REQUEST_SUPPORTED", "false")

    with pytest.raises(ValueError, match="embedding_provider_dimension_request_unsupported"):
        get_embedding_provider(
            "openai_compatible",
            dim=1024,
            model="fixed-native-model",
            base_url="https://embedding.example/v1",
            api_key="secret",
            timeout_seconds=7,
        )


def test_embedding_provider_factory_rejects_invalid_dimension_capability(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_DIMENSION_REQUEST_SUPPORTED", "sometimes")

    with pytest.raises(ValueError, match="embedding_provider_dimension_request_capability_invalid"):
        get_embedding_provider(
            "openai_compatible",
            dim=1024,
            model="text-embedding-3-small",
            base_url="https://embedding.example/v1",
            api_key="secret",
            timeout_seconds=7,
        )


@pytest.mark.parametrize(
    "embedding",
    [
        [0.0] * 1023,
        [0.0] * 1023 + [float("nan")],
        [0.0] * 1023 + [float("inf")],
        [0.0] * 1023 + ["not-a-number"],
    ],
)
def test_openai_compatible_embedding_provider_rejects_invalid_native_output(monkeypatch, embedding):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": embedding}]}).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="secret",
        model="text-embedding-3-small",
        dim=1024,
        timeout_seconds=7,
    )

    with pytest.raises(RuntimeError, match="embedding_provider_(dimension_mismatch|invalid_vector)"):
        provider.embed_texts(["first"])


def test_pg_hybrid_probe_proves_production_like_retrieval_path():
    probe = (ROOT / "backend" / "scripts" / "probe_knowledge_runtime_pg_hybrid.py").read_text(encoding="utf-8")

    assert 'KNOWLEDGE_EMBEDDING_PROVIDER"] = "openai_compatible"' in probe
    assert "get_embedding_provider(" in probe
    assert 'vector.get("storage") != "pgvector"' in probe
    assert '"postgres_fts" not in methods' in probe
    assert '"pgvector" not in methods' in probe
    assert 'KNOWLEDGE_VECTOR_FALLBACK_ALLOWED"] = "false"' in probe
