from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, models_control_plane  # noqa: F401
from app.db import Base
from app.models_control_plane import KnowledgeChunk, KnowledgeItem
from app.services.knowledge_runtime import runtime
from app.utils.time import utc_now


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _settings(**overrides):
    values = {
        "knowledge_embeddings_enabled": False,
        "knowledge_vector_fallback_allowed": True,
        "knowledge_embedding_provider": "deterministic_hash",
        "knowledge_embedding_model": "contract-1024",
        "knowledge_embedding_dim": 1024,
        "knowledge_embedding_base_url": "",
        "knowledge_embedding_api_key": "",
        "knowledge_embedding_api_key_file": "",
        "knowledge_embedding_timeout_seconds": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _add_knowledge(db, key: str, **overrides):
    now = utc_now()
    item_values = {
        "item_key": key,
        "title": f"{key} safe policy",
        "summary": "safe policy customer guidance",
        "status": "active",
        "source_type": "text",
        "knowledge_kind": "document",
        "tenant_id": "default",
        "brand_id": "default",
        "country_scope": "CH",
        "channel_scope": "website",
        "visibility": "customer",
        "shareability": "customer_visible",
        "authority_level": "policy",
        "risk_level": "low",
        "channel": "website",
        "audience_scope": "customer",
        "language": "en",
        "priority": 10,
        "published_body": "safe policy customer guidance",
        "published_normalized_text": "safe policy customer guidance",
        "published_version": 1,
        "published_at": now,
    }
    chunk_values = {
        "item_key": key,
        "title": f"{key} safe policy",
        "published_version": 1,
        "chunk_index": 0,
        "chunk_text": "safe policy customer guidance",
        "normalized_text": "safe policy customer guidance",
        "content_hash": f"hash-{key}",
        "tenant_id": "default",
        "brand_id": "default",
        "country_scope": "CH",
        "channel_scope": "website",
        "visibility": "customer",
        "shareability": "customer_visible",
        "authority_level": "policy",
        "risk_level": "low",
        "channel": "website",
        "audience_scope": "customer",
        "language": "en",
        "status": "active",
        "priority": 10,
        "source_type": "text",
        "knowledge_kind": "document",
        "fact_status": "draft",
        "answer_mode": "guided_answer",
    }
    item_overrides = dict(overrides.pop("item", {}))
    chunk_overrides = dict(overrides.pop("chunk", {}))
    item_values.update(overrides)
    item_values.update(item_overrides)
    chunk_values.update(overrides)
    chunk_values.update(chunk_overrides)

    item = KnowledgeItem(**item_values)
    db.add(item)
    db.flush()
    chunk = KnowledgeChunk(item_id=item.id, **chunk_values)
    db.add(chunk)
    db.flush()
    return item, chunk


def _retrieve(db):
    return runtime.retrieve_knowledge(
        db,
        query="safe policy",
        tenant_key="default",
        brand_id="default",
        country_scope="CH",
        channel_scope="website",
        channel="website",
        audience_scope="customer",
        language="en",
        limit=20,
    )


def test_retrieval_returns_only_published_customer_visible_isolated_rows(monkeypatch, db_session):
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    now = utc_now()

    _add_knowledge(db_session, "eligible")
    _add_knowledge(db_session, "draft", item={"status": "draft"})
    _add_knowledge(db_session, "expired", item={"valid_until": now - timedelta(seconds=1)})
    _add_knowledge(db_session, "internal", item={"visibility": "internal"}, chunk={"visibility": "internal"})
    _add_knowledge(db_session, "runtime-context", item={"shareability": "runtime_context"}, chunk={"shareability": "runtime_context"})
    _add_knowledge(db_session, "cross-tenant", item={"tenant_id": "other"}, chunk={"tenant_id": "other"})
    _add_knowledge(db_session, "cross-brand", item={"brand_id": "other"}, chunk={"brand_id": "other"})
    _add_knowledge(db_session, "cross-country", item={"country_scope": "DE"}, chunk={"country_scope": "DE"})
    _add_knowledge(db_session, "wrong-channel", item={"channel_scope": "email", "channel": "email"}, chunk={"channel_scope": "email", "channel": "email"})
    _add_knowledge(db_session, "wrong-audience", item={"audience_scope": "internal"}, chunk={"audience_scope": "internal"})
    _add_knowledge(db_session, "unpublished", item={"published_at": None})
    db_session.commit()

    result = _retrieve(db_session)

    assert [hit.item_key for hit in result.hits] == ["eligible"]
    assert result.trace["filters"]["tenant_id"] == "default"
    assert result.trace["filters"]["country_scope"] == "CH"
    assert result.trace["filters"]["shareability"] == ["customer_visible"]


def test_structured_knowledge_requires_item_and_chunk_approval(monkeypatch, db_session):
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    _add_knowledge(
        db_session,
        "approved-fact",
        item={
            "knowledge_kind": "business_fact",
            "fact_status": "approved",
            "fact_question": "safe policy",
            "fact_answer": "approved answer",
        },
        chunk={
            "knowledge_kind": "business_fact",
            "fact_status": "approved",
        },
    )
    _add_knowledge(
        db_session,
        "draft-chunk-fact",
        item={
            "knowledge_kind": "business_fact",
            "fact_status": "approved",
            "fact_question": "safe policy",
            "fact_answer": "unsafe draft answer",
        },
        chunk={
            "knowledge_kind": "business_fact",
            "fact_status": "draft",
        },
    )
    db_session.commit()

    result = _retrieve(db_session)

    assert [hit.item_key for hit in result.hits] == ["approved-fact"]
    assert result.hits[0].direct_answer == "approved answer"


def test_live_tracking_intent_fails_closed_before_db_or_embedding(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: _settings(knowledge_embeddings_enabled=True),
    )
    monkeypatch.setattr(
        runtime,
        "_candidate_rows",
        lambda *_args, **_kwargs: calls.append("db"),
    )
    monkeypatch.setattr(
        runtime,
        "get_embedding_provider",
        lambda *_args, **_kwargs: calls.append("provider"),
    )
    fake_db = SimpleNamespace()

    result = runtime.retrieve_knowledge(
        fake_db,
        query="Where is parcel CH120000005451 now?",
        tenant_key="default",
        brand_id="default",
        country_scope="CH",
        channel_scope="website",
        audience_scope="customer",
    )

    assert result.hits == []
    assert result.no_answer_reason == "live_tracking_requires_truth_source"
    assert result.trace["routing_target"] == "tracking_truth_layer"
    assert calls == []


@pytest.mark.parametrize(
    "query",
    [
        "What is the tracking number format?",
        "运单号格式是什么？",
        "CH120000005451",
    ],
)
def test_tracking_format_or_identifier_only_is_not_misclassified_as_live_status(query):
    assert runtime.is_live_tracking_intent(query) is False


def test_invalid_runtime_dimension_fails_closed_even_when_fallback_allowed(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: _settings(
            knowledge_embeddings_enabled=True,
            knowledge_embedding_dim=1536,
            knowledge_vector_fallback_allowed=True,
        ),
    )

    result = runtime.retrieve_knowledge(
        SimpleNamespace(),
        query="safe policy",
        tenant_key="default",
        brand_id="default",
        country_scope="CH",
        channel_scope="website",
        audience_scope="customer",
    )

    assert result.no_answer_reason == "knowledge_vector_dimension_mismatch"
    assert result.hits == []


@pytest.mark.parametrize(
    ("vectors", "reason"),
    [
        ([], "knowledge_embedding_cardinality_mismatch"),
        ([[0.0] * 383], "knowledge_embedding_vector_invalid"),
        ([[0.0] * 383 + [float("nan")]], "knowledge_embedding_vector_invalid"),
        ([[0.0] * 383 + [float("inf")]], "knowledge_embedding_vector_invalid"),
    ],
)
def test_runtime_provider_contract_errors_fail_closed(monkeypatch, db_session, vectors, reason):
    class Provider:
        def embed_texts(self, _texts):
            return vectors

    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: _settings(knowledge_embeddings_enabled=True),
    )
    monkeypatch.setattr(runtime, "get_embedding_provider", lambda *_args, **_kwargs: Provider())

    result = runtime.retrieve_knowledge(
        db_session,
        query="safe policy",
        tenant_key="default",
        brand_id="default",
        country_scope="CH",
        channel_scope="website",
        audience_scope="customer",
    )

    assert result.no_answer_reason == reason
    assert result.hits == []


def test_postgres_sql_enforces_customer_scope_and_valid_vector_contract():
    sql, params = runtime._postgres_candidate_sql(
        vector=True,
        tenant_id="default",
        brand_id="default",
        country_scope="CH",
        channel_scope="website",
        market_id=None,
        channel="website",
        audience_scope="customer",
        language="en",
    )

    assert "ki.published_at IS NOT NULL" in sql
    assert "kc.shareability = 'customer_visible'" in sql
    assert "ki.shareability = 'customer_visible'" in sql
    assert "kc.audience_scope = :audience_scope" in sql
    assert "ki.audience_scope = :audience_scope" in sql
    assert "kc.embedding_status = 'embedded'" in sql
    assert "kc.embedding_dim = :vector_dim" in sql
    assert params["vector_dim"] == 1024
