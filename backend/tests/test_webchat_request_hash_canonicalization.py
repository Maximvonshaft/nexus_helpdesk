from __future__ import annotations

import pytest

from app.services.webchat_fast_idempotency_db import compute_request_hash, normalize_recent_context

pytestmark = pytest.mark.fast_lane_v2_2_2


def _hash(**overrides):
    payload = {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-1",
        "client_message_id": "client-1",
        "body": " hello   world ",
        "recent_context": [{"role": "visitor", "text": " Hi "}, {"role": "assistant", "text": " Hello "}],
    }
    payload.update(overrides)
    return compute_request_hash(**payload)


def test_visitor_changes_do_not_affect_hash():
    assert _hash(visitor={"email": "a@example.com"}) == _hash(visitor={"email": "b@example.com"})


def test_empty_or_missing_optional_fields_normalized():
    assert _hash(tenant_key="", channel_key="") == compute_request_hash(
        tenant_key=None,
        channel_key=None,
        session_id="session-1",
        client_message_id="client-1",
        body="hello world",
        recent_context=[{"role": "customer", "text": "Hi"}, {"role": "ai", "text": "Hello"}],
    )


def test_whitespace_normalization():
    assert _hash(body="hello world") == _hash(body=" hello    world\n")


def test_role_alias_normalization():
    assert normalize_recent_context([{"role": "user", "text": "x"}, {"role": "agent", "text": "y"}]) == [
        {"role": "customer", "text": "x"},
        {"role": "ai", "text": "y"},
    ]


def test_json_key_order_stable():
    a = compute_request_hash(
        tenant_key="default",
        channel_key="website",
        session_id="session-1",
        client_message_id="client-1",
        body="hello world",
        recent_context=[{"text": "Hi", "role": "customer"}],
    )
    b = compute_request_hash(
        client_message_id="client-1",
        session_id="session-1",
        channel_key="website",
        tenant_key="default",
        recent_context=[{"role": "customer", "text": "Hi"}],
        body="hello world",
    )
    assert a == b


def test_different_body_changes_hash():
    assert _hash(body="a") != _hash(body="b")


def test_different_recent_context_changes_hash():
    assert _hash(recent_context=[{"role": "customer", "text": "a"}]) != _hash(recent_context=[{"role": "customer", "text": "b"}])
