from __future__ import annotations

import time
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..settings import get_settings
from ..utils.time import utc_now

settings = get_settings()
_MEMORY_BUCKETS: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    trusted = set(settings.trusted_proxy_ips or [])
    if client_host in trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return client_host


def _bucket_key(*, request: Request, tenant_key: str, conversation_id: str | None) -> str:
    scope = conversation_id or "init"
    return f"{tenant_key}:{scope}:{_client_ip(request)}"


def _enforce_memory(bucket_key: str) -> None:
    now = time.time()
    window = settings.webchat_rate_limit_window_seconds
    max_requests = settings.webchat_rate_limit_max_requests
    bucket = [ts for ts in _MEMORY_BUCKETS.get(bucket_key, []) if now - ts < window]
    if len(bucket) >= max_requests:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many webchat requests")
    bucket.append(now)
    _MEMORY_BUCKETS[bucket_key] = bucket


def _enforce_database(db: Session, bucket_key: str) -> None:
    now = utc_now()
    window_start = now - timedelta(seconds=settings.webchat_rate_limit_window_seconds)
    max_requests = settings.webchat_rate_limit_max_requests
    # Keep the statement deliberately small and portable across SQLite/PostgreSQL.
    existing = db.execute(
        text(
            "SELECT id, window_start, request_count FROM webchat_rate_limits "
            "WHERE bucket_key = :bucket_key ORDER BY id DESC LIMIT 1"
        ),
        {"bucket_key": bucket_key},
    ).mappings().first()
    if existing is None or existing["window_start"] is None or existing["window_start"] < window_start:
        db.execute(
            text(
                "INSERT INTO webchat_rate_limits "
                "(bucket_key, window_start, request_count, updated_at) "
                "VALUES (:bucket_key, :window_start, 1, :updated_at)"
            ),
            {"bucket_key": bucket_key, "window_start": now, "updated_at": now},
        )
        db.flush()
        return
    request_count = int(existing["request_count"] or 0)
    if request_count >= max_requests:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many webchat requests")
    db.execute(
        text(
            "UPDATE webchat_rate_limits SET request_count = request_count + 1, updated_at = :updated_at "
            "WHERE id = :id"
        ),
        {"id": existing["id"], "updated_at": now},
    )
    db.flush()


def enforce_webchat_rate_limit(
    db: Session,
    request: Request,
    *,
    tenant_key: str,
    conversation_id: str | None = None,
) -> None:
    bucket_key = _bucket_key(request=request, tenant_key=(tenant_key or "default"), conversation_id=conversation_id)
    if settings.webchat_rate_limit_backend == "memory":
        _enforce_memory(bucket_key)
        return
    _enforce_database(db, bucket_key)
