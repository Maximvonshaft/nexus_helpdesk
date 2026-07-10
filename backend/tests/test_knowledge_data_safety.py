from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.knowledge_runtime_v2 import data_safety_guard as guard


def _row_pair(**overrides):
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    item = SimpleNamespace(
        status="active",
        published_version=2,
        published_at=now - timedelta(days=1),
        tenant_id="tenant-a",
        brand_id="brand-a",
        country_scope="ME",
        channel_scope="webchat",
        channel="webchat",
        audience_scope="customer",
        visibility="customer",
        shareability="customer_visible",
        valid_from=now - timedelta(days=2),
        valid_until=now + timedelta(days=2),
        starts_at=None,
        ends_at=None,
        knowledge_kind="faq",
        fact_status="approved",
    )
    chunk = SimpleNamespace(
        status="active",
        published_version=2,
        tenant_id="tenant-a",
        brand_id="brand-a",
        country_scope="ME",
        channel_scope="webchat",
        channel="webchat",
        audience_scope="customer",
        visibility="customer",
        shareability="customer_visible",
        valid_from=now - timedelta(days=2),
        valid_until=now + timedelta(days=2),
        starts_at=None,
        ends_at=None,
        fact_status="approved",
    )
    for key, value in overrides.items():
        target, attribute = key.split("__", 1)
        setattr(item if target == "item" else chunk, attribute, value)
    return chunk, item, now


def _eligible(chunk, item, now):
    return guard._eligible(
        chunk,
        item,
        now=now,
        tenant_id="tenant-a",
        brand_id="brand-a",
        country_scope="ME",
        channel_scope="webchat",
        channel="webchat",
        audience_scope="customer",
    )


def test_live_tracking_intent_is_blocked_but_tracking_policy_is_not() -> None:
    assert guard.is_live_tracking_intent("Where is my parcel ME020000123456?") is True
    assert guard.is_live_tracking_intent("ME020000123456 这个包裹现在到哪里了") is True
    assert guard.is_live_tracking_intent("What is the tracking number format policy?") is False
    assert guard.is_live_tracking_intent("如何查询包裹？") is False


def test_retrieval_isolation_accepts_only_published_approved_customer_evidence() -> None:
    chunk, item, now = _row_pair()
    assert _eligible(chunk, item, now) is True

    blockers = [
        {"item__status": "draft"},
        {"item__published_version": 0},
        {"item__published_at": None},
        {"item__fact_status": "draft"},
        {"chunk__fact_status": "draft"},
        {"item__visibility": "internal"},
        {"chunk__shareability": "runtime_context"},
        {"item__tenant_id": "tenant-b"},
        {"chunk__brand_id": "brand-b"},
        {"item__country_scope": "CH"},
        {"chunk__channel_scope": "whatsapp"},
        {"item__audience_scope": "agent"},
        {"item__valid_until": now - timedelta(seconds=1)},
        {"chunk__ends_at": now - timedelta(seconds=1)},
    ]
    for values in blockers:
        blocked_chunk, blocked_item, _ = _row_pair(**values)
        assert _eligible(blocked_chunk, blocked_item, now) is False, values


def test_global_country_and_channel_are_valid_fallbacks() -> None:
    chunk, item, now = _row_pair(
        item__country_scope="GLOBAL",
        chunk__country_scope="GLOBAL",
        item__channel_scope="all",
        chunk__channel_scope="all",
        item__channel=None,
        chunk__channel=None,
    )
    assert _eligible(chunk, item, now) is True


def test_vector_dimension_drift_fails_closed(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(guard, "_ORIGINAL_RETRIEVE", lambda *_args, **_kwargs: sentinel)
    monkeypatch.setattr(
        guard,
        "get_settings",
        lambda: SimpleNamespace(knowledge_embeddings_enabled=True, knowledge_embedding_dim=1536),
    )

    result = guard.retrieve_knowledge_safe(query="What is the return policy?")

    assert result.hits == []
    assert result.no_answer_reason == "knowledge_vector_dimension_mismatch"


def test_live_tracking_block_precedes_database_or_provider_access(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(guard, "_ORIGINAL_RETRIEVE", lambda *_args, **_kwargs: calls.append(True))

    result = guard.retrieve_knowledge_safe(query="Where is parcel ME020000123456 now?")

    assert result.hits == []
    assert result.no_answer_reason == "live_tracking_requires_truth_source"
    assert calls == []
