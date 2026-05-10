from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from .webchat_fast_config import get_webchat_fast_settings

_BUCKETS: dict[str, list[float]] = {}


@dataclass(frozen=True)
class FastClientIdentity:
    tenant_key: str
    session_id: str
    client_ip: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _bucket_key(identity: FastClientIdentity) -> str:
    return f"{identity.tenant_key or 'default'}:{identity.session_id}:{identity.client_ip}"


def enforce_webchat_fast_rate_limit(request: Request, *, tenant_key: str, session_id: str) -> None:
    settings = get_webchat_fast_settings()
    now = time.time()
    key = _bucket_key(FastClientIdentity(tenant_key=tenant_key, session_id=session_id, client_ip=_client_ip(request)))
    bucket = [ts for ts in _BUCKETS.get(key, []) if now - ts < settings.rate_limit_window_seconds]
    if len(bucket) >= settings.rate_limit_max_requests:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many webchat fast reply requests")
    bucket.append(now)
    _BUCKETS[key] = bucket


def reset_webchat_fast_rate_limit_for_tests() -> None:
    _BUCKETS.clear()
