from __future__ import annotations

from app.services.openclaw_payload_hash import canonical_payload_json, payload_hash, payload_hash_from_json


def test_same_payload_same_hash() -> None:
    payload = {"sessionKey": "s1", "message": {"text": "hello", "n": 1}}
    assert payload_hash(payload) == payload_hash(payload)


def test_same_payload_different_key_order_same_hash() -> None:
    left = {"sessionKey": "s1", "message": {"text": "hello", "n": 1}, "type": "message"}
    right = {"type": "message", "message": {"n": 1, "text": "hello"}, "sessionKey": "s1"}
    assert canonical_payload_json(left) == canonical_payload_json(right)
    assert payload_hash(left) == payload_hash(right)


def test_payload_hash_from_json_uses_canonical_form() -> None:
    left = '{"a":1,"b":{"c":2}}'
    right = '{"b":{"c":2},"a":1}'
    assert payload_hash_from_json(left) == payload_hash_from_json(right)
