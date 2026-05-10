from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_payload_json(payload: Any) -> str:
    """Return stable JSON for idempotency hashing.

    This deliberately sorts keys and removes whitespace so semantically identical
    OpenClaw event payloads with different key order produce the same hash.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


def payload_hash_from_json(payload_json: str | None) -> str:
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        payload = payload_json or ""
    return payload_hash(payload)
