from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..settings import get_settings
from ..utils.client_ip import get_client_ip
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatConversation
from .webchat_rate_limit_policy import (
    WebchatRateLimitPolicy,
    load_webchat_preauth_rate_limit_policy,
)
from .webchat_tenant_binding import resolve_public_webchat_scope

settings = get_settings()
preauth_policy = load_webchat_preauth_rate_limit_policy(settings)
_MEMORY_BUCKETS: dict[str, list[float]] = {}


def _bucket_key(*, request: Request, tenant_key: str, conversation_id: str | None) -> str:
    scope = conversation_id or "init"
    raw_key = f"{tenant_key}:{scope}:{get_client_ip(request)}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _preauth_bucket_key(*, request: Request) -> str:
    raw_key = f"webchat-preauth:{get_client_ip(request)}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _authorized_policy() -> WebchatRateLimitPolicy:
    return WebchatRateLimitPolicy(
        window_seconds=settings.webchat_rate_limit_window_seconds,
        max_requests=settings.webchat_rate_limit_max_requests,
    )


def _enforce_memory(
    bucket_key: str,
    *,
    policy: WebchatRateLimitPolicy,
) -> None:
    now = time.time()
    bucket = [
        ts
        for ts in _MEMORY_BUCKETS.get(bucket_key, [])
        if now - ts < policy.window_seconds
    ]
    if len(bucket) >= policy.max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many webchat requests",
        )
    bucket.append(now)
    _MEMORY_BUCKETS[bucket_key] = bucket


def _normalize_database_window_start(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            return ensure_utc(datetime.fromisoformat(candidate))
        except ValueError as exc:
            raise RuntimeError(
                "invalid webchat rate-limit window_start timestamp"
            ) from exc
    raise RuntimeError(
        "unsupported webchat rate-limit window_start timestamp type"
    )


def _enforce_database(
    db: Session,
    bucket_key: str,
    *,
    policy: WebchatRateLimitPolicy,
) -> None:
    now = utc_now()
    window_start = now - timedelta(seconds=policy.window_seconds)
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        row = db.execute(
            text(
                "INSERT INTO webchat_rate_limits "
                "(bucket_key, window_start, request_count, updated_at) "
                "VALUES (:bucket_key, :now, 1, :now) "
                "ON CONFLICT (bucket_key) DO UPDATE SET "
                "window_start = CASE "
                "WHEN webchat_rate_limits.window_start IS NULL OR "
                "webchat_rate_limits.window_start < :window_start "
                "THEN :now ELSE webchat_rate_limits.window_start END, "
                "request_count = CASE "
                "WHEN webchat_rate_limits.window_start IS NULL OR "
                "webchat_rate_limits.window_start < :window_start "
                "THEN 1 ELSE webchat_rate_limits.request_count + 1 END, "
                "updated_at = :now "
                "RETURNING request_count"
            ),
            {
                "bucket_key": bucket_key,
                "now": now,
                "window_start": window_start,
            },
        ).mappings().first()
        request_count = int((row or {}).get("request_count") or 0)
        if request_count > policy.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many webchat requests",
            )
        db.flush()
        return

    existing = db.execute(
        text(
            "SELECT id, window_start, request_count FROM webchat_rate_limits "
            "WHERE bucket_key = :bucket_key ORDER BY id DESC LIMIT 1"
        ),
        {"bucket_key": bucket_key},
    ).mappings().first()
    if existing is None:
        db.execute(
            text(
                "INSERT INTO webchat_rate_limits "
                "(bucket_key, window_start, request_count, updated_at) "
                "VALUES (:bucket_key, :window_start, 1, :updated_at)"
            ),
            {
                "bucket_key": bucket_key,
                "window_start": now,
                "updated_at": now,
            },
        )
        db.flush()
        return

    existing_window_start = _normalize_database_window_start(
        existing["window_start"]
    )
    if existing_window_start is None or existing_window_start < window_start:
        db.execute(
            text(
                "UPDATE webchat_rate_limits "
                "SET window_start = :window_start, request_count = 1, "
                "updated_at = :updated_at WHERE id = :id"
            ),
            {
                "id": existing["id"],
                "window_start": now,
                "updated_at": now,
            },
        )
        db.flush()
        return

    request_count = int(existing["request_count"] or 0)
    if request_count >= policy.max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many webchat requests",
        )
    db.execute(
        text(
            "UPDATE webchat_rate_limits SET request_count = request_count + 1, "
            "updated_at = :updated_at WHERE id = :id"
        ),
        {"id": existing["id"], "updated_at": now},
    )
    db.flush()


def _enforce_database_committed(
    db: Session,
    bucket_key: str,
    *,
    policy: WebchatRateLimitPolicy,
) -> None:
    limiter_db = Session(
        bind=db.get_bind(),
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    try:
        _enforce_database(limiter_db, bucket_key, policy=policy)
        limiter_db.commit()
    except Exception:
        limiter_db.rollback()
        raise
    finally:
        limiter_db.close()


def enforce_webchat_preauth_rate_limit(
    db: Session,
    request: Request,
) -> None:
    bucket_key = _preauth_bucket_key(request=request)
    if settings.webchat_rate_limit_backend == "memory":
        _enforce_memory(bucket_key, policy=preauth_policy)
        return
    _enforce_database_committed(db, bucket_key, policy=preauth_policy)


def enforce_webchat_rate_limit(
    db: Session,
    request: Request,
    *,
    tenant_key: str,
    conversation_id: str | None = None,
    authorized_conversation: WebchatConversation | None = None,
) -> None:
    verified_scope = resolve_public_webchat_scope(
        db,
        request=request,
        requested_tenant_key=tenant_key,
        requested_channel_key=request.query_params.get("channel_key") or None,
        conversation_id=conversation_id,
        authorized_conversation=authorized_conversation,
    )
    bucket_key = _bucket_key(
        request=request,
        tenant_key=verified_scope.tenant_key,
        conversation_id=conversation_id,
    )
    policy = _authorized_policy()
    if settings.webchat_rate_limit_backend == "memory":
        _enforce_memory(bucket_key, policy=policy)
        return
    _enforce_database(db, bucket_key, policy=policy)
