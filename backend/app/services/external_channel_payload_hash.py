from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import Column, String


def canonical_payload_json(payload: Any) -> str:
    """Return stable JSON for idempotency hashing.

    This deliberately sorts keys and removes whitespace so semantically identical
    ExternalChannel event payloads with different key order produce the same hash.
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


def ensure_external_channel_unresolved_payload_hash_mapping() -> None:
    """Ensure ExternalChannelUnresolvedEvent maps the migration-added payload_hash column.

    The model file is intentionally large and shared across multiple active PRs.
    This small helper gives the runtime and tests an explicit alignment point for
    the new column without changing unrelated model declarations.
    """
    from ..models import ExternalChannelUnresolvedEvent

    if hasattr(ExternalChannelUnresolvedEvent, "payload_hash") and "payload_hash" in ExternalChannelUnresolvedEvent.__table__.c:
        return
    column = Column("payload_hash", String(64), nullable=False, index=True)
    if "payload_hash" not in ExternalChannelUnresolvedEvent.__table__.c:
        ExternalChannelUnresolvedEvent.__table__.append_column(column)
    else:
        column = ExternalChannelUnresolvedEvent.__table__.c.payload_hash
    try:
        ExternalChannelUnresolvedEvent.__mapper__.add_property("payload_hash", column)
    except Exception:
        # The mapper may already have been configured by another import path.
        # In that case the table column is still available for migrations/tests.
        if not hasattr(ExternalChannelUnresolvedEvent, "payload_hash"):
            setattr(ExternalChannelUnresolvedEvent, "payload_hash", column)


# Apply on import so any service importing the canonical hash helper also gets
# the model/migration alignment required for live dedupe queries.
ensure_external_channel_unresolved_payload_hash_mapping()
