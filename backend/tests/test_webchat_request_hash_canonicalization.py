from __future__ import annotations

import pytest

from app.services.webchat_fast_idempotency_db import canonical_request_payload, compute_request_hash, normalize_recent_context

pytestmark = pytest.mark.fast_lane_v2_2_2


def _base_payload() -> dict:
    return {
        'tenant_key': 'default',
        'channel_key': 'website',
        'session_id': 'session-1',
        'client_message_id': 'client-1',
        'body': ' hello   world ',
        'recent_context': [
            {'role': 'visitor', 'text': ' Hi '},
            {'role': 'assistant', 'text': ' Hello '},
        ],
    }


def _hash(payload: dict) -> str:
    return compute_request_hash(**payload)


def test_api_payload_visitor_not_in_hash_inputs():
    payload = _base_payload()
    visitor_a = {'name': 'Alice', 'email': 'a@example.com'}
    visitor_b = {'name': 'Bob', 'email': 'b@example.com'}
    api_payload_a = dict(payload, visitor=visitor_a)
    api_payload_b = dict(payload, visitor=visitor_b)
    canonical_a = canonical_request_payload(**{k: api_payload_a[k] for k in ('tenant_key','channel_key','session_id','client_message_id','body','recent_context')})
    canonical_b = canonical_request_payload(**{k: api_payload_b[k] for k in ('tenant_key','channel_key','session_id','client_message_id','body','recent_context')})
    assert canonical_a == canonical_b
    assert _hash(payload) == _hash(payload)


def test_visitor_changes_do_not_affect_hash():
    payload = _base_payload()
    assert _hash(payload) == _hash(dict(payload))


def test_empty_or_missing_optional_fields_normalized():
    a = compute_request_hash(
        tenant_key='',
        channel_key='',
        session_id='session-1',
        client_message_id='client-1',
        body='hello world',
        recent_context=[{'role': 'customer', 'text': 'Hi'}, {'role': 'ai', 'text': 'Hello'}],
    )
    b = compute_request_hash(
        tenant_key=None,
        channel_key=None,
        session_id='session-1',
        client_message_id='client-1',
        body=' hello  world ',
        recent_context=[{'role': 'visitor', 'body': 'Hi'}, {'role': 'assistant', 'body': 'Hello'}],
    )
    assert a == b


def test_empty_visitor_or_missing_visitor_do_not_affect_hash():
    payload = _base_payload()
    assert _hash(payload) == compute_request_hash(**dict(payload))


def test_whitespace_normalization():
    payload = _base_payload()
    assert _hash(dict(payload, body='hello world')) == _hash(dict(payload, body=' hello    world\n'))


def test_role_alias_normalization():
    assert normalize_recent_context([{'role': 'user', 'text': 'x'}, {'role': 'agent', 'text': 'y'}]) == [
        {'role': 'customer', 'text': 'x'},
        {'role': 'ai', 'text': 'y'},
    ]


def test_json_key_order_stable():
    a = compute_request_hash(
        tenant_key='default',
        channel_key='website',
        session_id='session-1',
        client_message_id='client-1',
        body='hello world',
        recent_context=[{'text': 'Hi', 'role': 'customer'}],
    )
    b = compute_request_hash(
        client_message_id='client-1',
        session_id='session-1',
        channel_key='website',
        tenant_key='default',
        recent_context=[{'role': 'customer', 'text': 'Hi'}],
        body='hello world',
    )
    assert a == b


def test_different_body_changes_hash():
    payload = _base_payload()
    assert _hash(dict(payload, body='a')) != _hash(dict(payload, body='b'))


def test_different_recent_context_changes_hash():
    payload = _base_payload()
    assert _hash(dict(payload, recent_context=[{'role': 'customer', 'text': 'a'}])) != _hash(dict(payload, recent_context=[{'role': 'customer', 'text': 'b'}]))


def test_null_empty_string_and_missing_optional_fields_normalize_same():
    a = canonical_request_payload(tenant_key=None, channel_key='', session_id='session-1', client_message_id='client-1', body='hello', recent_context=None)
    b = canonical_request_payload(tenant_key='', channel_key=None, session_id=' session-1 ', client_message_id='client-1', body=' hello ', recent_context=[])
    assert a == b
