from __future__ import annotations

import hashlib

def is_stream_rollout_selected(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    rollout_percent: int,
) -> bool:
    if rollout_percent <= 0:
        return False
    if rollout_percent >= 100:
        return True

    bucket_source = f"{tenant_key}:{channel_key}:{session_id}"
    digest = hashlib.sha256(bucket_source.encode("utf-8")).hexdigest()
    # take last 8 hex characters to form an integer
    integer_val = int(digest[-8:], 16)
    bucket = integer_val % 100

    return bucket < rollout_percent

