from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.dialects import postgresql

from app.models_control_plane import KnowledgeChunk
from app.services.knowledge_runtime_v2.embeddings import OpenAICompatibleEmbeddingProvider, vector_literal
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
    assert KnowledgeChunk.__table__.c.embedding_vector.type.dialect_impl(dialect).compile(dialect=dialect) == "vector(384)"


def test_openai_compatible_embedding_provider_parses_ordered_vectors(monkeypatch):
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
    provider = OpenAICompatibleEmbeddingProvider(base_url="https://embedding.example/v1", api_key="secret", model="text-embedding-3-small", dim=3, timeout_seconds=7)

    vectors = provider.embed_texts(["first", "second"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert captured == {
        "url": "https://embedding.example/v1/embeddings",
        "timeout": 7,
        "authorization": "Bearer secret",
        "body": {"model": "text-embedding-3-small", "input": ["first", "second"]},
    }
    assert vector_literal([1, 0.25, -0.5]) == "[1.00000000,0.25000000,-0.50000000]"


def test_pg_hybrid_action_proves_production_like_retrieval_path():
    workflow = (ROOT / ".github" / "workflows" / "knowledge-runtime-pg-hybrid.yml").read_text(encoding="utf-8")
    probe = (ROOT / "backend" / "scripts" / "probe_knowledge_runtime_pg_hybrid.py").read_text(encoding="utf-8")

    assert "pgvector/pgvector:pg16" in workflow
    assert "alembic upgrade head" in workflow
    assert "probe_knowledge_runtime_pg_hybrid.py" in workflow
    assert 'KNOWLEDGE_EMBEDDING_PROVIDER"] = "openai_compatible"' in probe
    assert "get_embedding_provider(" in probe
    assert 'vector.get("storage") != "pgvector"' in probe
    assert '"postgres_fts" not in methods' in probe
    assert '"pgvector" not in methods' in probe
    assert 'KNOWLEDGE_VECTOR_FALLBACK_ALLOWED"] = "false"' in probe
