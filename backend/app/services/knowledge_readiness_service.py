from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import func, text

from ..models_control_plane import KNOWLEDGE_VECTOR_DIMENSION, KnowledgeChunk, KnowledgeItem
from ..operator_models import OperatorTask
from ..settings import get_settings
from ..utils.time import utc_now

READINESS_SCHEMA_VERSION = "nexus_knowledge_readiness_v2"
EXPECTED_VECTOR_TYPE = f"vector({KNOWLEDGE_VECTOR_DIMENSION})"
MAX_DISTINCT_COVERAGE = 1_000
MAX_COUNT = 1_000_000_000
MAX_REASON_CODES = 24
TERMINAL_GAP_STATUSES = ("resolved", "dropped", "replayed", "replay_failed", "cancelled")
STRUCTURED_KINDS = {"faq", "business_fact"}


@dataclass
class KnowledgeReadinessSnapshot:
    active_items: int = 0
    approved_items: int = 0
    published_items: int = 0
    customer_published_items: int = 0
    eligible_items: int = 0
    future_items: int = 0
    expired_items: int = 0
    stale_items: int = 0
    owner_missing_items: int = 0
    review_date_missing_items: int = 0
    indexed_items: int = 0
    total_chunks: int = 0
    embedded_chunks: int = 0
    invalid_dimension_chunks: int = 0
    failed_embedding_chunks: int = 0
    distinct_tenants: int = 0
    distinct_brands: int = 0
    distinct_countries: int = 0
    distinct_channels: int = 0
    distinct_audiences: int = 0
    tenant_overflow: bool = False
    brand_overflow: bool = False
    country_overflow: bool = False
    channel_overflow: bool = False
    audience_overflow: bool = False
    expected_tenant_covered: bool | None = None
    expected_brand_covered: bool | None = None
    expected_country_covered: bool | None = None
    expected_channel_covered: bool | None = None
    expected_audience_covered: bool | None = None
    oldest_published_age_days: int | None = None
    canary_item: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class KnowledgeReadinessReport:
    status: str
    ready: bool
    evaluated_at: str
    reasons: tuple[str, ...]
    counts: dict[str, int]
    coverage: dict[str, Any]
    freshness: dict[str, Any]
    gates: dict[str, dict[str, Any]]
    metrics: tuple[dict[str, Any], ...]
    schema_version: str = READINESS_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "ready": self.ready,
            "evaluated_at": self.evaluated_at,
            "reasons": list(self.reasons),
            "counts": dict(self.counts),
            "coverage": dict(self.coverage),
            "freshness": dict(self.freshness),
            "gates": {name: dict(value) for name, value in self.gates.items()},
            "metrics": [dict(metric) for metric in self.metrics],
        }

    def as_admin_read_model(self) -> dict[str, Any]:
        # The report intentionally contains aggregates and governed reason codes only.
        return self.as_dict()


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _scope(value: Any, default: str) -> str:
    return str(value or default).strip()


def _validity_state(item: Any, now: datetime) -> str:
    starts = (
        _as_utc(getattr(item, "valid_from", None)),
        _as_utc(getattr(item, "starts_at", None)),
    )
    ends = (
        _as_utc(getattr(item, "valid_until", None)),
        _as_utc(getattr(item, "ends_at", None)),
    )
    if any(value is not None and now < value for value in starts):
        return "future"
    if any(value is not None and now >= value for value in ends):
        return "expired"
    return "current"


def _is_customer_visible(item: Any) -> bool:
    return (
        _scope(getattr(item, "visibility", None), "internal").lower() == "customer"
        and _scope(getattr(item, "shareability", None), "internal").lower() == "customer_visible"
        and _scope(getattr(item, "audience_scope", None), "internal").lower() == "customer"
    )


def _is_approved(item: Any) -> bool:
    return _scope(getattr(item, "fact_status", None), "draft").lower() == "approved"


def _approval_policy_passes(item: Any) -> bool:
    kind = _scope(getattr(item, "knowledge_kind", None), "document").lower()
    return kind not in STRUCTURED_KINDS or _is_approved(item)


def _is_published(item: Any) -> bool:
    return int(getattr(item, "published_version", 0) or 0) > 0 and getattr(item, "published_at", None) is not None


def _is_indexed(item: Any) -> bool:
    return (
        _is_published(item)
        and int(getattr(item, "indexed_version", 0) or 0) == int(getattr(item, "published_version", 0) or 0)
        and getattr(item, "indexed_at", None) is not None
        and int(getattr(item, "chunk_count", 0) or 0) > 0
    )


def _has_owner(item: Any) -> bool:
    return any(getattr(item, name, None) is not None for name in ("published_by", "updated_by", "created_by"))


def _is_stale(item: Any, *, now: datetime, freshness_cutoff: datetime) -> bool:
    review_due = _as_utc(getattr(item, "review_due_at", None))
    published_at = _as_utc(getattr(item, "published_at", None))
    return bool(
        (review_due is not None and review_due < now)
        or _validity_state(item, now) != "current"
        or published_at is None
        or published_at < freshness_cutoff
    )


def _bounded_add(values: set[str], value: str) -> bool:
    if value in values:
        return False
    if len(values) >= MAX_DISTINCT_COVERAGE:
        return True
    values.add(value)
    return False


def collect_knowledge_snapshot(
    db: Any,
    *,
    now: datetime,
    freshness_days: int,
    expected_tenant: str | None = None,
    expected_brand: str | None = None,
    expected_country: str | None = None,
    expected_channel: str | None = None,
    expected_audience: str | None = "customer",
) -> KnowledgeReadinessSnapshot:
    snapshot = KnowledgeReadinessSnapshot(
        expected_tenant_covered=None if not expected_tenant else False,
        expected_brand_covered=None if not expected_brand else False,
        expected_country_covered=None if not expected_country else False,
        expected_channel_covered=None if not expected_channel else False,
        expected_audience_covered=None if not expected_audience else False,
    )
    freshness_cutoff = now - timedelta(days=max(1, freshness_days))
    tenants: set[str] = set()
    brands: set[str] = set()
    countries: set[str] = set()
    channels: set[str] = set()
    audiences: set[str] = set()
    current_versions: dict[int, int] = {}
    eligible_ids: set[int] = set()
    oldest_age = 0

    item_query = db.query(KnowledgeItem).filter(KnowledgeItem.status == "active")
    iterator: Iterable[Any] = item_query.yield_per(500) if hasattr(item_query, "yield_per") else item_query.all()
    for item in iterator:
        snapshot.active_items += 1
        approved = _is_approved(item)
        published = _is_published(item)
        customer_visible = _is_customer_visible(item)
        validity = _validity_state(item, now)
        if approved:
            snapshot.approved_items += 1
        if published:
            snapshot.published_items += 1
            published_at = _as_utc(getattr(item, "published_at", None))
            if published_at is not None:
                oldest_age = max(oldest_age, max(0, (now - published_at).days))

        customer_published = published and customer_visible
        eligible = customer_published and validity == "current" and _approval_policy_passes(item)
        if customer_published:
            snapshot.customer_published_items += 1
            if validity == "future":
                snapshot.future_items += 1
            elif validity == "expired":
                snapshot.expired_items += 1
            if _is_stale(item, now=now, freshness_cutoff=freshness_cutoff):
                snapshot.stale_items += 1
            if not _has_owner(item):
                snapshot.owner_missing_items += 1
            if getattr(item, "review_due_at", None) is None:
                snapshot.review_date_missing_items += 1

        if eligible:
            snapshot.eligible_items += 1
            item_id = int(item.id)
            eligible_ids.add(item_id)
            current_versions[item_id] = int(item.published_version)
            if snapshot.canary_item is None:
                snapshot.canary_item = item
            tenant = _scope(getattr(item, "tenant_id", None), "default")
            brand = _scope(getattr(item, "brand_id", None), "default")
            country = _scope(getattr(item, "country_scope", None), "GLOBAL").upper()
            channel = _scope(getattr(item, "channel_scope", None), "all").lower()
            audience = _scope(getattr(item, "audience_scope", None), "internal").lower()
            snapshot.tenant_overflow = _bounded_add(tenants, tenant) or snapshot.tenant_overflow
            snapshot.brand_overflow = _bounded_add(brands, brand) or snapshot.brand_overflow
            snapshot.country_overflow = _bounded_add(countries, country) or snapshot.country_overflow
            snapshot.channel_overflow = _bounded_add(channels, channel) or snapshot.channel_overflow
            snapshot.audience_overflow = _bounded_add(audiences, audience) or snapshot.audience_overflow
            if expected_tenant:
                snapshot.expected_tenant_covered = snapshot.expected_tenant_covered or tenant == expected_tenant
            if expected_brand:
                snapshot.expected_brand_covered = snapshot.expected_brand_covered or brand == expected_brand
            if expected_country:
                snapshot.expected_country_covered = snapshot.expected_country_covered or country in {expected_country.upper(), "GLOBAL"}
            if expected_channel:
                snapshot.expected_channel_covered = snapshot.expected_channel_covered or channel in {expected_channel.lower(), "all"}
            if expected_audience:
                snapshot.expected_audience_covered = snapshot.expected_audience_covered or audience == expected_audience.lower()
            if _is_indexed(item):
                snapshot.indexed_items += 1

    snapshot.distinct_tenants = len(tenants)
    snapshot.distinct_brands = len(brands)
    snapshot.distinct_countries = len(countries)
    snapshot.distinct_channels = len(channels)
    snapshot.distinct_audiences = len(audiences)
    snapshot.oldest_published_age_days = oldest_age if snapshot.published_items else None

    chunk_query = db.query(KnowledgeChunk).filter(KnowledgeChunk.status == "active")
    chunk_iterator: Iterable[Any] = chunk_query.yield_per(1000) if hasattr(chunk_query, "yield_per") else chunk_query.all()
    for chunk in chunk_iterator:
        item_id = int(getattr(chunk, "item_id", 0) or 0)
        if item_id not in eligible_ids or int(getattr(chunk, "published_version", 0) or 0) != current_versions[item_id]:
            continue
        snapshot.total_chunks += 1
        status = _scope(getattr(chunk, "embedding_status", None), "pending").lower()
        if status == "failed":
            snapshot.failed_embedding_chunks += 1
        if status == "embedded":
            vector_present = bool(getattr(chunk, "embedding", None) or getattr(chunk, "embedding_vector", None))
            if int(getattr(chunk, "embedding_dim", 0) or 0) == KNOWLEDGE_VECTOR_DIMENSION and vector_present:
                snapshot.embedded_chunks += 1
            else:
                snapshot.invalid_dimension_chunks += 1
        elif getattr(chunk, "embedding_dim", None) not in (None, KNOWLEDGE_VECTOR_DIMENSION):
            snapshot.invalid_dimension_chunks += 1
    return snapshot


def provider_readiness(settings: Any) -> dict[str, Any]:
    runtime_version = _scope(getattr(settings, "knowledge_runtime_version", None), "legacy").lower()
    enabled = bool(getattr(settings, "knowledge_embeddings_enabled", False))
    provider = _scope(getattr(settings, "knowledge_embedding_provider", None), "unconfigured").lower()
    model_present = bool(_scope(getattr(settings, "knowledge_embedding_model", None), ""))
    dim_ok = int(getattr(settings, "knowledge_embedding_dim", 0) or 0) == KNOWLEDGE_VECTOR_DIMENSION
    credential_present = bool(
        getattr(settings, "knowledge_embedding_api_key", None)
        or getattr(settings, "knowledge_embedding_api_key_file", None)
        or provider in {"deterministic_hash", "hash", "test"}
    )
    production = _scope(getattr(settings, "app_env", None), "development").lower() == "production"
    real_provider = provider not in {"deterministic_hash", "hash", "test", "unconfigured"}
    reasons: list[str] = []
    if runtime_version != "v2":
        reasons.append("knowledge_runtime_v2_required")
    if not enabled:
        reasons.append("knowledge_embeddings_disabled")
    if not model_present:
        reasons.append("knowledge_embedding_model_missing")
    if not dim_ok:
        reasons.append("knowledge_embedding_dimension_mismatch")
    if not credential_present:
        reasons.append("knowledge_embedding_credentials_missing")
    if production and not real_provider:
        reasons.append("production_embedding_provider_not_ready")
    return {
        "status": "ready" if not reasons else "not_ready",
        "ready": not reasons,
        "reason_codes": reasons,
        "provider_class": "real" if real_provider else "test_or_unconfigured",
        "dimension": KNOWLEDGE_VECTOR_DIMENSION if dim_ok else 0,
    }


def index_readiness(db: Any, settings: Any, snapshot: KnowledgeReadinessSnapshot) -> dict[str, Any]:
    dialect = _scope(getattr(db.get_bind().dialect, "name", None), "unknown").lower()
    production = _scope(getattr(settings, "app_env", None), "development").lower() == "production"
    reasons: list[str] = []
    status = "ready"
    storage = "postgresql_pgvector" if dialect == "postgresql" else "sqlite_text_fallback" if dialect == "sqlite" else "unsupported"
    if snapshot.eligible_items <= 0 or snapshot.total_chunks <= 0:
        reasons.append("knowledge_index_has_no_customer_chunks")
    if snapshot.indexed_items < snapshot.eligible_items:
        reasons.append("knowledge_items_not_fully_indexed")
    if bool(getattr(settings, "knowledge_embeddings_enabled", False)):
        if snapshot.embedded_chunks < snapshot.total_chunks:
            reasons.append("knowledge_chunks_not_fully_embedded")
        if snapshot.invalid_dimension_chunks:
            reasons.append("knowledge_chunk_dimension_mismatch")
        if snapshot.failed_embedding_chunks:
            reasons.append("knowledge_chunk_embedding_failures")

    if dialect == "postgresql":
        try:
            vector_type = db.execute(text("""
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'knowledge_chunks'
                  AND a.attname = 'embedding_vector'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY (n.nspname = current_schema()) DESC
                LIMIT 1
            """)).scalar()
            index_names = set(db.execute(text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'knowledge_chunks'
                  AND indexname IN (
                    'ix_knowledge_chunks_search_tsvector_gin',
                    'ix_knowledge_chunks_embedding_vector_ivfflat'
                  )
            """)).scalars().all())
        except Exception:
            return {
                "status": "unavailable",
                "ready": False,
                "reason_codes": ["knowledge_index_introspection_unavailable"],
                "storage": storage,
            }
        if str(vector_type or "").lower() != EXPECTED_VECTOR_TYPE:
            reasons.append("postgresql_vector_type_mismatch")
        expected_indexes = {
            "ix_knowledge_chunks_search_tsvector_gin",
            "ix_knowledge_chunks_embedding_vector_ivfflat",
        }
        if not expected_indexes.issubset(index_names):
            reasons.append("postgresql_knowledge_indexes_missing")
    elif production:
        reasons.append("production_requires_postgresql_pgvector")
    elif dialect == "sqlite":
        status = "degraded"
        reasons.append("nonproduction_sqlite_fallback")
    else:
        reasons.append("knowledge_index_storage_unsupported")

    if any(reason != "nonproduction_sqlite_fallback" for reason in reasons):
        status = "not_ready"
    return {
        "status": status,
        "ready": status in {"ready", "degraded"},
        "reason_codes": reasons,
        "storage": storage,
    }


def retrieval_readiness(
    db: Any,
    snapshot: KnowledgeReadinessSnapshot,
    *,
    retriever: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    item = snapshot.canary_item
    if item is None:
        return {"status": "not_ready", "ready": False, "reason_codes": ["knowledge_retrieval_canary_missing"]}
    query = _scope(getattr(item, "fact_question", None) or getattr(item, "title", None) or getattr(item, "summary", None), "")
    if not query:
        return {"status": "not_ready", "ready": False, "reason_codes": ["knowledge_retrieval_canary_query_missing"]}
    if retriever is None:
        from .knowledge_runtime_v2 import retrieve_knowledge as retriever
    try:
        result = retriever(
            db,
            query=query,
            tenant_key=getattr(item, "tenant_id", None),
            brand_id=getattr(item, "brand_id", None),
            country_scope=getattr(item, "country_scope", None),
            channel_scope=getattr(item, "channel_scope", None),
            market_id=getattr(item, "market_id", None),
            channel=getattr(item, "channel", None),
            audience_scope="customer",
            language=getattr(item, "language", None),
            limit=3,
        )
    except Exception:
        return {"status": "unavailable", "ready": False, "reason_codes": ["knowledge_retrieval_unavailable"]}
    hits = list(getattr(result, "hits", None) or [])
    matched = any(int(getattr(hit, "item_id", 0) or 0) == int(item.id) for hit in hits)
    return {
        "status": "ready" if matched else "not_ready",
        "ready": matched,
        "reason_codes": [] if matched else ["knowledge_retrieval_canary_miss"],
    }


def live_tracking_boundary_readiness(db: Any, *, retriever: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Prove a synthetic live-status request is routed away from Knowledge."""
    if retriever is None:
        from .knowledge_runtime_v2 import retrieve_knowledge as retriever
    try:
        result = retriever(
            db,
            query="Where is parcel NX000000000000 now?",
            tenant_key="readiness-probe",
            brand_id="readiness-probe",
            country_scope="GLOBAL",
            channel_scope="webchat",
            audience_scope="customer",
            limit=1,
        )
    except Exception:
        return {"status": "unavailable", "ready": False, "reason_codes": ["live_tracking_boundary_unavailable"]}
    blocked = (
        not list(getattr(result, "hits", None) or [])
        and getattr(result, "no_answer_reason", None) == "live_tracking_requires_truth_source"
    )
    return {
        "status": "ready" if blocked else "not_ready",
        "ready": blocked,
        "reason_codes": [] if blocked else ["live_tracking_entered_knowledge_path"],
    }


def knowledge_gap_state(db: Any, snapshot: KnowledgeReadinessSnapshot, retrieval_gate: dict[str, Any]) -> dict[str, Any]:
    source_available = True
    try:
        task_count = int(
            db.query(func.count(OperatorTask.id))
            .filter(OperatorTask.task_type == "knowledge_gap", OperatorTask.status.notin_(TERMINAL_GAP_STATUSES))
            .scalar()
            or 0
        )
    except Exception:
        source_available = False
        task_count = 0
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            rollback()
    derived = (
        max(0, snapshot.eligible_items - snapshot.indexed_items)
        + snapshot.stale_items
        + snapshot.owner_missing_items
        + snapshot.review_date_missing_items
        + (0 if retrieval_gate.get("ready") else 1)
    )
    total = task_count + derived
    reasons: list[str] = []
    if not source_available:
        reasons.append("knowledge_gap_source_unavailable")
    if total:
        reasons.append("knowledge_gaps_present")
    return {
        "status": "ready" if total == 0 and source_available else "degraded",
        "ready": total == 0 and source_available,
        "reason_codes": reasons,
        "open_gap_count": min(max(total, 0), MAX_COUNT),
        "source_available": source_available,
    }


def _data_gate(snapshot: KnowledgeReadinessSnapshot) -> dict[str, Any]:
    reasons: list[str] = []
    if snapshot.approved_items <= 0:
        reasons.append("approved_knowledge_missing")
    if snapshot.published_items <= 0:
        reasons.append("published_knowledge_missing")
    if snapshot.customer_published_items <= 0:
        reasons.append("customer_visible_knowledge_missing")
    if snapshot.eligible_items <= 0:
        reasons.append("eligible_customer_knowledge_missing")
    for covered, code in (
        (snapshot.expected_tenant_covered, "tenant_coverage_missing"),
        (snapshot.expected_brand_covered, "brand_coverage_missing"),
        (snapshot.expected_country_covered, "country_coverage_missing"),
        (snapshot.expected_channel_covered, "channel_coverage_missing"),
        (snapshot.expected_audience_covered, "audience_coverage_missing"),
    ):
        if covered is False:
            reasons.append(code)
    return {"status": "ready" if not reasons else "not_ready", "ready": not reasons, "reason_codes": reasons}


def _freshness_gate(snapshot: KnowledgeReadinessSnapshot) -> dict[str, Any]:
    reasons: list[str] = []
    if snapshot.future_items:
        reasons.append("future_knowledge_present")
    if snapshot.expired_items:
        reasons.append("expired_knowledge_present")
    if snapshot.stale_items:
        reasons.append("stale_knowledge_present")
    if snapshot.owner_missing_items:
        reasons.append("knowledge_owner_missing")
    if snapshot.review_date_missing_items:
        reasons.append("knowledge_review_date_missing")
    return {"status": "ready" if not reasons else "not_ready", "ready": not reasons, "reason_codes": reasons}


def _bounded_count(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), MAX_COUNT))
    except (TypeError, ValueError, OverflowError):
        return 0


def assess_knowledge_readiness(
    snapshot: KnowledgeReadinessSnapshot,
    *,
    evaluated_at: datetime,
    provider_gate: dict[str, Any],
    index_gate: dict[str, Any],
    retrieval_gate: dict[str, Any],
    tracking_boundary_gate: dict[str, Any],
    gap_gate: dict[str, Any],
) -> KnowledgeReadinessReport:
    gates = {
        "data": _data_gate(snapshot),
        "freshness": _freshness_gate(snapshot),
        "provider": provider_gate,
        "index": index_gate,
        "retrieval": retrieval_gate,
        "live_tracking_boundary": tracking_boundary_gate,
        "knowledge_gap": gap_gate,
    }
    statuses = {str(gate.get("status") or "unavailable") for gate in gates.values()}
    if "unavailable" in statuses:
        status = "unavailable"
    elif "not_ready" in statuses:
        status = "not_ready"
    elif "degraded" in statuses:
        status = "degraded"
    else:
        status = "ready"
    reasons: list[str] = []
    for gate in gates.values():
        for reason in gate.get("reason_codes") or []:
            normalized = str(reason)[:120]
            if normalized not in reasons:
                reasons.append(normalized)
            if len(reasons) >= MAX_REASON_CODES:
                break
        if len(reasons) >= MAX_REASON_CODES:
            break

    count_names = (
        "active_items", "approved_items", "published_items", "customer_published_items",
        "eligible_items", "future_items", "expired_items", "stale_items",
        "owner_missing_items", "review_date_missing_items", "indexed_items", "total_chunks",
        "embedded_chunks", "failed_embedding_chunks", "invalid_dimension_chunks",
    )
    counts = {name: _bounded_count(getattr(snapshot, name)) for name in count_names}
    counts["open_knowledge_gaps"] = _bounded_count(gap_gate.get("open_gap_count", 0))
    coverage = {
        "tenant_count": min(snapshot.distinct_tenants, MAX_DISTINCT_COVERAGE),
        "brand_count": min(snapshot.distinct_brands, MAX_DISTINCT_COVERAGE),
        "country_count": min(snapshot.distinct_countries, MAX_DISTINCT_COVERAGE),
        "channel_count": min(snapshot.distinct_channels, MAX_DISTINCT_COVERAGE),
        "audience_count": min(snapshot.distinct_audiences, MAX_DISTINCT_COVERAGE),
        "tenant_count_overflow": snapshot.tenant_overflow,
        "brand_count_overflow": snapshot.brand_overflow,
        "country_count_overflow": snapshot.country_overflow,
        "channel_count_overflow": snapshot.channel_overflow,
        "audience_count_overflow": snapshot.audience_overflow,
        "expected_tenant_covered": snapshot.expected_tenant_covered,
        "expected_brand_covered": snapshot.expected_brand_covered,
        "expected_country_covered": snapshot.expected_country_covered,
        "expected_channel_covered": snapshot.expected_channel_covered,
        "expected_audience_covered": snapshot.expected_audience_covered,
    }
    denominator = snapshot.customer_published_items
    freshness = {
        "oldest_published_age_days": snapshot.oldest_published_age_days,
        "stale_items": counts["stale_items"],
        "owner_coverage_percent": round(100.0 * max(0, denominator - snapshot.owner_missing_items) / denominator, 2) if denominator else 0.0,
        "review_date_coverage_percent": round(100.0 * max(0, denominator - snapshot.review_date_missing_items) / denominator, 2) if denominator else 0.0,
    }
    metrics = (
        {"name": "nexus_knowledge_readiness", "value": 1 if status == "ready" else 0, "labels": {"status": status}},
        {"name": "nexus_knowledge_published_items", "value": counts["published_items"], "labels": {}},
        {"name": "nexus_knowledge_approved_items", "value": counts["approved_items"], "labels": {}},
        {"name": "nexus_knowledge_stale_items", "value": counts["stale_items"], "labels": {}},
        {"name": "nexus_knowledge_open_gaps", "value": counts["open_knowledge_gaps"], "labels": {}},
    )
    return KnowledgeReadinessReport(
        status=status,
        ready=status == "ready",
        evaluated_at=evaluated_at.astimezone(timezone.utc).isoformat(),
        reasons=tuple(reasons),
        counts=counts,
        coverage=coverage,
        freshness=freshness,
        gates=gates,
        metrics=metrics,
    )


def unavailable_report(*, evaluated_at: datetime | None = None) -> KnowledgeReadinessReport:
    now = _as_utc(evaluated_at) or _as_utc(utc_now()) or datetime.now(timezone.utc)
    snapshot = KnowledgeReadinessSnapshot()
    gate = {"status": "unavailable", "ready": False, "reason_codes": ["knowledge_readiness_unavailable"]}
    return KnowledgeReadinessReport(
        status="unavailable",
        ready=False,
        evaluated_at=now.isoformat(),
        reasons=("knowledge_readiness_unavailable",),
        counts={
            **{
                name: 0 for name in (
                    "active_items", "approved_items", "published_items", "customer_published_items",
                    "eligible_items", "future_items", "expired_items", "stale_items",
                    "owner_missing_items", "review_date_missing_items", "indexed_items", "total_chunks",
                    "embedded_chunks", "failed_embedding_chunks", "invalid_dimension_chunks",
                )
            },
            "open_knowledge_gaps": 0,
        },
        coverage={
            "tenant_count": 0, "brand_count": 0, "country_count": 0, "channel_count": 0, "audience_count": 0,
            "tenant_count_overflow": False, "brand_count_overflow": False, "country_count_overflow": False,
            "channel_count_overflow": False, "audience_count_overflow": False,
            "expected_tenant_covered": snapshot.expected_tenant_covered,
            "expected_brand_covered": snapshot.expected_brand_covered,
            "expected_country_covered": snapshot.expected_country_covered,
            "expected_channel_covered": snapshot.expected_channel_covered,
            "expected_audience_covered": snapshot.expected_audience_covered,
        },
        freshness={
            "oldest_published_age_days": None,
            "stale_items": 0,
            "owner_coverage_percent": 0.0,
            "review_date_coverage_percent": 0.0,
        },
        gates={"runtime": gate},
        metrics=({"name": "nexus_knowledge_readiness", "value": 0, "labels": {"status": "unavailable"}},),
    )


def build_knowledge_readiness(
    db: Any,
    *,
    settings: Any | None = None,
    expected_tenant: str | None = None,
    expected_brand: str | None = None,
    expected_country: str | None = None,
    expected_channel: str | None = None,
    expected_audience: str | None = "customer",
    freshness_days: int = 90,
    retriever: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> KnowledgeReadinessReport:
    evaluated_at = _as_utc(now) or _as_utc(utc_now()) or datetime.now(timezone.utc)
    resolved_settings = settings or get_settings()
    try:
        snapshot = collect_knowledge_snapshot(
            db,
            now=evaluated_at,
            freshness_days=freshness_days,
            expected_tenant=expected_tenant,
            expected_brand=expected_brand,
            expected_country=expected_country,
            expected_channel=expected_channel,
            expected_audience=expected_audience,
        )
        provider_gate = provider_readiness(resolved_settings)
        index_gate = index_readiness(db, resolved_settings, snapshot)
        retrieval_gate = (
            retrieval_readiness(db, snapshot, retriever=retriever)
            if provider_gate.get("ready") and index_gate.get("ready")
            else {"status": "not_ready", "ready": False, "reason_codes": ["knowledge_retrieval_prerequisites_not_ready"]}
        )
        tracking_boundary_gate = live_tracking_boundary_readiness(db, retriever=retriever)
        gap_gate = knowledge_gap_state(db, snapshot, retrieval_gate)
        return assess_knowledge_readiness(
            snapshot,
            evaluated_at=evaluated_at,
            provider_gate=provider_gate,
            index_gate=index_gate,
            retrieval_gate=retrieval_gate,
            tracking_boundary_gate=tracking_boundary_gate,
            gap_gate=gap_gate,
        )
    except Exception:
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            rollback()
        return unavailable_report(evaluated_at=evaluated_at)
