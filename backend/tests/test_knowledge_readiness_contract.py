from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services import knowledge_readiness_service as service

NOW = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)


def _snapshot(**overrides):
    values = dict(
        active_items=2,
        approved_items=2,
        published_items=2,
        customer_published_items=2,
        eligible_items=2,
        stale_items=0,
        owner_missing_items=0,
        review_date_missing_items=0,
        indexed_items=2,
        total_chunks=4,
        embedded_chunks=4,
        invalid_dimension_chunks=0,
        failed_embedding_chunks=0,
        distinct_tenants=1,
        distinct_countries=1,
        distinct_channels=1,
        expected_tenant_covered=True,
        expected_country_covered=True,
        expected_channel_covered=True,
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


def test_healthy_readiness_is_ready_and_low_cardinality() -> None:
    report = service.assess_knowledge_readiness(
        _snapshot(),
        evaluated_at=NOW,
        provider_gate=_gate(),
        index_gate=_gate(storage="postgresql_pgvector"),
        retrieval_gate=_gate(),
        gap_gate=_gate(open_gap_count=0, source_available=True),
    )
    payload = report.as_admin_read_model()

    assert report.status == "ready"
    assert report.ready is True
    assert payload["counts"]["approved_items"] == 2
    assert payload["coverage"]["tenant_count"] == 1
    assert all(set(metric["labels"]).issubset({"status"}) for metric in payload["metrics"])
    serialized = json.dumps(payload)
    for forbidden in ("item_key", "title", "source_url", "api_key", "tracking_number"):
        assert forbidden not in serialized


def test_zero_approved_or_published_items_is_not_ready() -> None:
    report = service.assess_knowledge_readiness(
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
        evaluated_at=NOW,
        provider_gate=_gate(),
        index_gate=_gate("not_ready", ["knowledge_index_has_no_customer_chunks"]),
        retrieval_gate=_gate("not_ready", ["knowledge_retrieval_canary_missing"]),
        gap_gate=_gate("degraded", ["knowledge_gaps_present"], open_gap_count=1),
    )

    assert report.status == "not_ready"
    assert "approved_knowledge_missing" in report.reasons
    assert "published_knowledge_missing" in report.reasons


def test_stale_ownerless_and_missing_review_date_fail_closed() -> None:
    report = service.assess_knowledge_readiness(
        _snapshot(stale_items=1, owner_missing_items=1, review_date_missing_items=1),
        evaluated_at=NOW,
        provider_gate=_gate(),
        index_gate=_gate(),
        retrieval_gate=_gate(),
        gap_gate=_gate("degraded", ["knowledge_gaps_present"], open_gap_count=3),
    )

    assert report.status == "not_ready"
    assert set(report.gates["freshness"]["reason_codes"]) == {
        "stale_knowledge_present",
        "knowledge_owner_missing",
        "knowledge_review_date_missing",
    }


def test_coverage_counts_are_bounded_and_expected_scope_is_fail_closed() -> None:
    report = service.assess_knowledge_readiness(
        _snapshot(
            distinct_tenants=5000,
            distinct_countries=5000,
            distinct_channels=5000,
            tenant_overflow=True,
            country_overflow=True,
            channel_overflow=True,
            expected_country_covered=False,
        ),
        evaluated_at=NOW,
        provider_gate=_gate(),
        index_gate=_gate(),
        retrieval_gate=_gate(),
        gap_gate=_gate(open_gap_count=0),
    )

    assert report.coverage["tenant_count"] == service.MAX_DISTINCT_COVERAGE
    assert report.coverage["country_count_overflow"] is True
    assert report.status == "not_ready"
    assert "country_coverage_missing" in report.reasons


def test_provider_readiness_never_exposes_credentials() -> None:
    settings = SimpleNamespace(
        knowledge_runtime_version="v2",
        knowledge_embeddings_enabled=True,
        knowledge_embedding_provider="openai_compatible",
        knowledge_embedding_model="model-a",
        knowledge_embedding_dim=384,
        knowledge_embedding_api_key="super-secret-key",
        knowledge_embedding_api_key_file=None,
        app_env="production",
    )

    gate = service.provider_readiness(settings)

    assert gate["status"] == "ready"
    assert "super-secret-key" not in repr(gate)
    assert set(gate) == {"status", "ready", "reason_codes", "provider_class", "dimension"}


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
    def __init__(self, dialect, vector_type="vector(384)", indexes=None):
        self.dialect = dialect
        self.vector_type = vector_type
        self.indexes = indexes or []
        self.calls = 0

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return _ScalarResult(scalar=self.vector_type)
        return _ScalarResult(rows=self.indexes)


def _settings(app_env="production"):
    return SimpleNamespace(app_env=app_env, knowledge_embeddings_enabled=True)


def test_postgresql_index_readiness_checks_type_and_both_indexes() -> None:
    indexes = [
        "ix_knowledge_chunks_search_tsvector_gin",
        "ix_knowledge_chunks_embedding_vector_ivfflat",
    ]
    ready = service.index_readiness(_IndexDB("postgresql", indexes=indexes), _settings(), _snapshot())
    broken = service.index_readiness(_IndexDB("postgresql", vector_type="vector(1536)", indexes=indexes[:1]), _settings(), _snapshot())

    assert ready["status"] == "ready"
    assert broken["status"] == "not_ready"
    assert "postgresql_vector_type_mismatch" in broken["reason_codes"]
    assert "postgresql_knowledge_indexes_missing" in broken["reason_codes"]


def test_sqlite_is_degraded_in_nonproduction_and_not_ready_in_production() -> None:
    nonproduction = service.index_readiness(_IndexDB("sqlite"), _settings("test"), _snapshot())
    production = service.index_readiness(_IndexDB("sqlite"), _settings("production"), _snapshot())

    assert nonproduction["status"] == "degraded"
    assert nonproduction["ready"] is True
    assert production["status"] == "not_ready"
    assert "production_requires_postgresql_pgvector" in production["reason_codes"]


def test_retrieval_canary_ready_and_miss_contracts() -> None:
    item = SimpleNamespace(
        id=7,
        fact_question="What is the return policy?",
        title="Return policy",
        summary=None,
        tenant_id="tenant-a",
        brand_id="brand-a",
        country_scope="ME",
        channel_scope="webchat",
        market_id=None,
        channel="webchat",
        language="en",
    )
    snapshot = _snapshot(canary_item=item)
    ready = service.retrieval_readiness(
        object(),
        snapshot,
        retriever=lambda *_args, **_kwargs: SimpleNamespace(hits=[SimpleNamespace(item_id=7)]),
    )
    miss = service.retrieval_readiness(
        object(),
        snapshot,
        retriever=lambda *_args, **_kwargs: SimpleNamespace(hits=[]),
    )

    assert ready["status"] == "ready"
    assert miss["status"] == "not_ready"
    assert miss["reason_codes"] == ["knowledge_retrieval_canary_miss"]


def test_unavailable_runtime_returns_redacted_fixed_contract() -> None:
    report = service.unavailable_report(evaluated_at=NOW)
    payload = report.as_admin_read_model()

    assert report.status == "unavailable"
    assert report.reasons == ("knowledge_readiness_unavailable",)
    assert payload["gates"] == {
        "runtime": {
            "status": "unavailable",
            "ready": False,
            "reason_codes": ["knowledge_readiness_unavailable"],
        }
    }


def test_probe_exit_code_contract() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_knowledge_readiness.py"
    spec = importlib.util.spec_from_file_location("knowledge_readiness_probe_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    assert module.exit_code_for_status("ready") == 0
    assert module.exit_code_for_status("degraded") == 1
    assert module.exit_code_for_status("degraded", allow_degraded=True) == 0
    assert module.exit_code_for_status("not_ready") == 1
    assert module.exit_code_for_status("unavailable") == 2


def test_retrieval_exception_is_unavailable_without_error_text() -> None:
    item = SimpleNamespace(
        id=7,
        fact_question="What is the return policy?",
        title="Return policy",
        summary=None,
        tenant_id="tenant-a",
        brand_id="brand-a",
        country_scope="ME",
        channel_scope="webchat",
        market_id=None,
        channel="webchat",
        language="en",
    )

    def _broken(*_args, **_kwargs):
        raise RuntimeError("database password=secret")

    gate = service.retrieval_readiness(object(), _snapshot(canary_item=item), retriever=_broken)

    assert gate == {
        "status": "unavailable",
        "ready": False,
        "reason_codes": ["knowledge_retrieval_unavailable"],
    }
    assert "secret" not in repr(gate)


def test_build_readiness_fails_closed_when_database_is_unavailable() -> None:
    class _BrokenDB:
        rolled_back = False

        def query(self, _model):
            raise RuntimeError("postgresql://user:password@host/db")

        def rollback(self):
            self.rolled_back = True

    db = _BrokenDB()
    report = service.build_knowledge_readiness(
        db,
        settings=SimpleNamespace(),
        now=NOW,
    )

    assert report.status == "unavailable"
    assert report.reasons == ("knowledge_readiness_unavailable",)
    assert db.rolled_back is True
    assert "password" not in json.dumps(report.as_dict())
