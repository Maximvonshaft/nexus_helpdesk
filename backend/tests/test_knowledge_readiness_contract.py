from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, models_control_plane  # noqa: F401
from app.db import Base
from app.models_control_plane import KnowledgeChunk, KnowledgeItem
from app.services import knowledge_readiness_service as service

NOW = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)


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


def _snapshot(**overrides):
    values = dict(
        active_items=2,
        approved_items=2,
        published_items=2,
        customer_published_items=2,
        eligible_items=2,
        future_items=0,
        expired_items=0,
        stale_items=0,
        owner_missing_items=0,
        review_date_missing_items=0,
        indexed_items=2,
        total_chunks=4,
        embedded_chunks=4,
        invalid_dimension_chunks=0,
        failed_embedding_chunks=0,
        distinct_tenants=1,
        distinct_brands=1,
        distinct_countries=1,
        distinct_channels=1,
        distinct_audiences=1,
        expected_tenant_covered=True,
        expected_brand_covered=True,
        expected_country_covered=True,
        expected_channel_covered=True,
        expected_audience_covered=True,
        oldest_published_age_days=10,
    )
    values.update(overrides)
    return service.KnowledgeReadinessSnapshot(**values)


def _gate(status="ready", reasons=None, **extra):
    return {
        "status": status,
        "ready": status == "ready",
        "reason_codes": list(reasons or []),
        **extra,
    }


def _assess(snapshot=None, **gates):
    values = {
        "provider_gate": _gate(),
        "index_gate": _gate(storage="postgresql_pgvector"),
        "retrieval_gate": _gate(),
        "tracking_boundary_gate": _gate(),
        "gap_gate": _gate(open_gap_count=0, source_available=True),
    }
    values.update(gates)
    return service.assess_knowledge_readiness(snapshot or _snapshot(), evaluated_at=NOW, **values)


def test_healthy_readiness_is_ready_redacted_and_low_cardinality() -> None:
    payload = _assess().as_admin_read_model()

    assert payload["schema_version"] == "nexus_knowledge_readiness_v2"
    assert payload["status"] == "ready"
    assert payload["ready"] is True
    assert payload["counts"]["approved_items"] == 2
    assert payload["coverage"]["brand_count"] == 1
    assert payload["gates"]["live_tracking_boundary"]["ready"] is True
    assert all(set(metric["labels"]).issubset({"status"}) for metric in payload["metrics"])
    serialized = json.dumps(payload)
    for forbidden in (
        "item_key", "title", "source_url", "api_key", "password", "tracking_number",
        "chunk_text", "published_body", "provider_payload",
    ):
        assert forbidden not in serialized


def test_zero_approved_published_or_customer_visible_items_is_not_ready() -> None:
    report = _assess(
        _snapshot(
            active_items=0,
            approved_items=0,
            published_items=0,
            customer_published_items=0,
            eligible_items=0,
            indexed_items=0,
            total_chunks=0,
            embedded_chunks=0,
        ),
        index_gate=_gate("not_ready", ["knowledge_index_has_no_customer_chunks"]),
        retrieval_gate=_gate("not_ready", ["knowledge_retrieval_canary_missing"]),
        gap_gate=_gate("degraded", ["knowledge_gaps_present"], open_gap_count=1),
    )

    assert report.status == "not_ready"
    assert {
        "approved_knowledge_missing",
        "published_knowledge_missing",
        "customer_visible_knowledge_missing",
        "eligible_customer_knowledge_missing",
    }.issubset(report.reasons)


def test_scope_coverage_is_bounded_and_each_expected_scope_fails_closed() -> None:
    report = _assess(
        _snapshot(
            distinct_tenants=5_000,
            distinct_brands=5_000,
            distinct_countries=5_000,
            distinct_channels=5_000,
            distinct_audiences=5_000,
            tenant_overflow=True,
            brand_overflow=True,
            country_overflow=True,
            channel_overflow=True,
            audience_overflow=True,
            expected_tenant_covered=False,
            expected_brand_covered=False,
            expected_country_covered=False,
            expected_channel_covered=False,
            expected_audience_covered=False,
        )
    )

    assert report.status == "not_ready"
    assert all(report.coverage[f"{scope}_count"] == service.MAX_DISTINCT_COVERAGE for scope in ("tenant", "brand", "country", "channel", "audience"))
    assert {
        "tenant_coverage_missing", "brand_coverage_missing", "country_coverage_missing",
        "channel_coverage_missing", "audience_coverage_missing",
    }.issubset(report.reasons)


def test_future_expired_stale_ownerless_and_unreviewed_content_fails_closed() -> None:
    report = _assess(
        _snapshot(
            future_items=1,
            expired_items=1,
            stale_items=2,
            owner_missing_items=1,
            review_date_missing_items=1,
        )
    )

    assert report.status == "not_ready"
    assert set(report.gates["freshness"]["reason_codes"]) == {
        "future_knowledge_present",
        "expired_knowledge_present",
        "stale_knowledge_present",
        "knowledge_owner_missing",
        "knowledge_review_date_missing",
    }


def test_provider_contract_requires_v2_enabled_model_dimension_credentials_and_real_production_provider() -> None:
    ready = service.provider_readiness(SimpleNamespace(
        knowledge_runtime_version="v2",
        knowledge_embeddings_enabled=True,
        knowledge_embedding_provider="openai_compatible",
        knowledge_embedding_model="model-a",
        knowledge_embedding_dim=1024,
        knowledge_embedding_api_key="super-secret-key",
        knowledge_embedding_api_key_file=None,
        app_env="production",
    ))
    broken = service.provider_readiness(SimpleNamespace(
        knowledge_runtime_version="legacy",
        knowledge_embeddings_enabled=False,
        knowledge_embedding_provider="deterministic_hash",
        knowledge_embedding_model="",
        knowledge_embedding_dim=1_536,
        knowledge_embedding_api_key=None,
        knowledge_embedding_api_key_file=None,
        app_env="production",
    ))

    assert ready["status"] == "ready"
    assert "super-secret-key" not in repr(ready)
    assert broken["status"] == "not_ready"
    assert "knowledge_embedding_dimension_mismatch" in broken["reason_codes"]
    assert "production_embedding_provider_not_ready" in broken["reason_codes"]


class _ScalarResult:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _IndexDB:
    def __init__(self, dialect, vector_type="vector(1024)", indexes=None, fail=False):
        self.dialect = dialect
        self.vector_type = vector_type
        self.indexes = indexes or []
        self.calls = 0
        self.fail = fail

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, _statement):
        if self.fail:
            raise RuntimeError("postgresql://user:secret@host/db")
        self.calls += 1
        if self.calls == 1:
            return _ScalarResult(scalar=self.vector_type)
        return _ScalarResult(rows=self.indexes)


def _settings(app_env="production", embeddings=True):
    return SimpleNamespace(app_env=app_env, knowledge_embeddings_enabled=embeddings)


def test_postgresql_index_contract_checks_type_indexes_vectors_and_fail_closed_introspection() -> None:
    indexes = [
        "ix_knowledge_chunks_search_tsvector_gin",
        "ix_knowledge_chunks_embedding_vector_ivfflat",
    ]
    ready = service.index_readiness(_IndexDB("postgresql", indexes=indexes), _settings(), _snapshot())
    broken = service.index_readiness(
        _IndexDB("postgresql", vector_type="vector(1536)", indexes=indexes[:1]),
        _settings(),
        _snapshot(invalid_dimension_chunks=1, embedded_chunks=3, failed_embedding_chunks=1),
    )
    unavailable = service.index_readiness(_IndexDB("postgresql", fail=True), _settings(), _snapshot())

    assert ready["status"] == "ready"
    assert broken["status"] == "not_ready"
    assert "postgresql_vector_type_mismatch" in broken["reason_codes"]
    assert "postgresql_knowledge_indexes_missing" in broken["reason_codes"]
    assert "knowledge_chunk_dimension_mismatch" in broken["reason_codes"]
    assert unavailable == {
        "status": "unavailable",
        "ready": False,
        "reason_codes": ["knowledge_index_introspection_unavailable"],
        "storage": "postgresql_pgvector",
    }
    assert "secret" not in repr(unavailable)


def test_sqlite_is_degraded_only_outside_production() -> None:
    nonproduction = service.index_readiness(_IndexDB("sqlite"), _settings("test"), _snapshot())
    production = service.index_readiness(_IndexDB("sqlite"), _settings("production"), _snapshot())

    assert nonproduction["status"] == "degraded"
    assert nonproduction["ready"] is True
    assert production["status"] == "not_ready"
    assert "production_requires_postgresql_pgvector" in production["reason_codes"]


def _canary():
    return SimpleNamespace(
        id=7,
        fact_question="What is the return policy?",
        title="Return policy",
        summary=None,
        tenant_id="tenant-a",
        brand_id="brand-a",
        country_scope="CH",
        channel_scope="webchat",
        market_id=None,
        channel="webchat",
        language="en",
    )


def test_retrieval_canary_ready_miss_and_exception_contracts() -> None:
    ready = service.retrieval_readiness(
        object(), _snapshot(canary_item=_canary()),
        retriever=lambda *_args, **_kwargs: SimpleNamespace(hits=[SimpleNamespace(item_id=7)]),
    )
    miss = service.retrieval_readiness(
        object(), _snapshot(canary_item=_canary()),
        retriever=lambda *_args, **_kwargs: SimpleNamespace(hits=[]),
    )

    def _broken(*_args, **_kwargs):
        raise RuntimeError("authorization=Bearer secret")

    unavailable = service.retrieval_readiness(object(), _snapshot(canary_item=_canary()), retriever=_broken)

    assert ready["status"] == "ready"
    assert miss["reason_codes"] == ["knowledge_retrieval_canary_miss"]
    assert unavailable["reason_codes"] == ["knowledge_retrieval_unavailable"]
    assert "secret" not in repr(unavailable)


def test_live_tracking_probe_must_be_routed_to_truth_layer_without_hits() -> None:
    calls = []

    def _blocked(_db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(hits=[], no_answer_reason="live_tracking_requires_truth_source")

    ready = service.live_tracking_boundary_readiness(object(), retriever=_blocked)
    leaked = service.live_tracking_boundary_readiness(
        object(),
        retriever=lambda *_args, **_kwargs: SimpleNamespace(
            hits=[SimpleNamespace(item_id=1)], no_answer_reason=None,
        ),
    )

    assert ready == {"status": "ready", "ready": True, "reason_codes": []}
    assert calls[0]["query"] == "Where is parcel NX000000000000 now?"
    assert calls[0]["tenant_key"] == "readiness-probe"
    assert leaked == {
        "status": "not_ready",
        "ready": False,
        "reason_codes": ["live_tracking_entered_knowledge_path"],
    }


def _add_item(db, key: str, **overrides):
    values = {
        "item_key": key,
        "title": f"{key} policy",
        "status": "active",
        "source_type": "text",
        "knowledge_kind": "business_fact",
        "fact_status": "approved",
        "tenant_id": "tenant-a",
        "brand_id": "brand-a",
        "country_scope": "CH",
        "channel_scope": "webchat",
        "visibility": "customer",
        "shareability": "customer_visible",
        "audience_scope": "customer",
        "language": "en",
        "published_body": "Safe synthetic policy.",
        "published_normalized_text": "safe synthetic policy",
        "published_version": 1,
        "published_at": NOW - timedelta(days=10),
        "published_by": 1,
        "review_due_at": NOW + timedelta(days=30),
        "indexed_version": 1,
        "indexed_at": NOW - timedelta(days=9),
        "chunk_count": 1,
    }
    values.update(overrides.pop("item", {}))
    item = KnowledgeItem(**values)
    db.add(item)
    db.flush()
    chunk_values = {
        "item_id": item.id,
        "item_key": key,
        "title": item.title,
        "published_version": item.published_version,
        "chunk_index": 0,
        "chunk_text": "Safe synthetic policy.",
        "normalized_text": "safe synthetic policy",
        "content_hash": f"hash-{key}",
        "tenant_id": item.tenant_id,
        "brand_id": item.brand_id,
        "country_scope": item.country_scope,
        "channel_scope": item.channel_scope,
        "visibility": item.visibility,
        "shareability": item.shareability,
        "audience_scope": item.audience_scope,
        "status": "active",
        "knowledge_kind": item.knowledge_kind,
        "fact_status": item.fact_status,
        "embedding": [0.0] * 1024,
        "embedding_vector": "[0.0]",
        "embedding_dim": 1024,
        "embedding_status": "embedded",
    }
    chunk_values.update(overrides.pop("chunk", {}))
    db.add(KnowledgeChunk(**chunk_values))
    db.flush()
    return item


def test_snapshot_excludes_draft_expired_internal_and_cross_scope_content(db_session) -> None:
    _add_item(db_session, "eligible")
    _add_item(db_session, "draft", item={"fact_status": "draft"}, chunk={"fact_status": "draft"})
    _add_item(db_session, "expired", item={"valid_until": NOW - timedelta(seconds=1)})
    _add_item(db_session, "internal", item={"visibility": "internal"}, chunk={"visibility": "internal"})
    _add_item(db_session, "other-tenant", item={"tenant_id": "tenant-b"}, chunk={"tenant_id": "tenant-b"})
    _add_item(db_session, "other-country", item={"country_scope": "DE"}, chunk={"country_scope": "DE"})
    db_session.commit()

    snapshot = service.collect_knowledge_snapshot(
        db_session,
        now=NOW,
        freshness_days=90,
        expected_tenant="tenant-a",
        expected_brand="brand-a",
        expected_country="CH",
        expected_channel="webchat",
        expected_audience="customer",
    )

    assert snapshot.active_items == 6
    assert snapshot.customer_published_items == 5
    assert snapshot.eligible_items == 3
    assert snapshot.expired_items == 1
    assert snapshot.indexed_items == 3
    assert snapshot.total_chunks == 3
    assert snapshot.distinct_tenants == 2
    assert snapshot.distinct_countries == 2
    assert snapshot.expected_tenant_covered is True
    assert snapshot.expected_country_covered is True


def test_unavailable_runtime_is_fixed_redacted_contract() -> None:
    payload = service.unavailable_report(evaluated_at=NOW).as_admin_read_model()

    assert payload["status"] == "unavailable"
    assert payload["ready"] is False
    assert payload["reasons"] == ["knowledge_readiness_unavailable"]
    assert payload["gates"] == {
        "runtime": {
            "status": "unavailable",
            "ready": False,
            "reason_codes": ["knowledge_readiness_unavailable"],
        }
    }


def _probe_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_knowledge_readiness.py"
    spec = importlib.util.spec_from_file_location("knowledge_readiness_probe_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_exit_status_and_deterministic_bounded_json_contract() -> None:
    probe = _probe_module()
    report = _assess()
    first = probe.encode_report(report)
    second = probe.encode_report(report)

    assert first == second
    assert len(first.encode("utf-8")) <= probe.MAX_PROBE_BYTES
    assert isinstance(json.loads(first), dict)
    assert probe.exit_code_for_status("ready") == 0
    assert probe.exit_code_for_status("degraded") == 1
    assert probe.exit_code_for_status("degraded", allow_degraded=True) == 0
    assert probe.exit_code_for_status("not_ready") == 1
    assert probe.exit_code_for_status("unavailable") == 2


def test_probe_serialization_error_falls_back_to_unavailable() -> None:
    probe = _probe_module()

    class _BrokenReport:
        def as_admin_read_model(self):
            return {"status": float("nan")}

    payload = json.loads(probe.encode_report(_BrokenReport()))

    assert payload["status"] == "unavailable"
    assert payload["ready"] is False


def test_database_failure_rolls_back_and_never_exposes_error_text() -> None:
    class _BrokenDB:
        rolled_back = False

        def query(self, _model):
            raise RuntimeError("postgresql://user:password@host/db")

        def rollback(self):
            self.rolled_back = True

    db = _BrokenDB()
    report = service.build_knowledge_readiness(db, settings=SimpleNamespace(), now=NOW)

    assert report.status == "unavailable"
    assert db.rolled_back is True
    assert "password" not in json.dumps(report.as_dict())


def test_shell_probe_has_no_speedaf_secret_or_live_lookup_dependency() -> None:
    root = Path(__file__).resolve().parents[2]
    shell = (root / "scripts" / "nexus_knowledge_runtime_v2_readiness_probe.sh").read_text(encoding="utf-8")

    assert "probe_knowledge_readiness.py" in shell
    assert "SPEEDAF_MCP_TEST" not in shell
    assert "lookup_tracking_fact" not in shell
    assert "tracking_fact_service" not in shell
