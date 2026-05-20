from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..db import get_current_request_id
from ..models import AdminActionRateLimitBucket
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_admin_audit
from .observability import LOGGER

settings = get_settings()


def _bucket_key(*, actor_id: int, action_key: str) -> str:
    return f"{actor_id}:{action_key.strip().lower()}"


def _rate_limit_detail(*, action_key: str, request_id: str | None) -> dict[str, str]:
    return {
        "message": "Admin action rate limit exceeded",
        "action": action_key,
        "request_id": request_id or "unknown",
    }


def _normalize_for_compare(value):
    if value is None:
        return None
    tzinfo = getattr(value, "tzinfo", None)
    if tzinfo is not None and value.utcoffset() is not None:
        return value.replace(tzinfo=None)
    return value


def _log_rate_limit_hit(
    db: Session,
    *,
    actor_id: int,
    action_key: str,
    request_id: str | None,
    request_count: int,
    max_requests: int,
) -> None:
    effective_request_id = request_id or get_current_request_id()
    detail = _rate_limit_detail(action_key=action_key, request_id=effective_request_id)
    log_admin_audit(
        db,
        actor_id=actor_id,
        action="admin_action.rate_limited",
        target_type="admin_action",
        target_id=None,
        old_value={
            "action_key": action_key,
            "request_count": request_count,
            "window_seconds": settings.admin_action_rate_limit_window_seconds,
            "limit": max_requests,
        },
        new_value=detail,
    )
    db.commit()
    LOGGER.warning(
        "admin_action_rate_limited",
        extra={"event_payload": {
            "actor_id": actor_id,
            "action_key": action_key,
            "request_id": effective_request_id or "unknown",
            "request_count": request_count,
            "limit": max_requests,
            "window_seconds": settings.admin_action_rate_limit_window_seconds,
        }},
    )
    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


def _enforce_with_atomic_upsert(
    db: Session,
    *,
    actor_id: int,
    action_key: str,
    max_requests: int,
    request_id: str | None,
) -> bool:
    now = utc_now()
    window_start = now - timedelta(seconds=settings.admin_action_rate_limit_window_seconds)
    bucket_key = _bucket_key(actor_id=actor_id, action_key=action_key)

    row = db.execute(
        text(
            """
            INSERT INTO admin_action_rate_limits (bucket_key, window_start, request_count, updated_at)
            VALUES (:bucket_key, :now, 1, :now)
            ON CONFLICT(bucket_key) DO UPDATE SET
                window_start = CASE
                    WHEN admin_action_rate_limits.window_start < :window_start THEN excluded.window_start
                    ELSE admin_action_rate_limits.window_start
                END,
                request_count = CASE
                    WHEN admin_action_rate_limits.window_start < :window_start THEN 1
                    ELSE admin_action_rate_limits.request_count + 1
                END,
                updated_at = excluded.updated_at
            RETURNING request_count, window_start
            """
        ),
        {"bucket_key": bucket_key, "now": now, "window_start": window_start},
    ).mappings().one()
    db.commit()

    request_count = int(row["request_count"] or 0)
    if request_count > max_requests:
        _log_rate_limit_hit(
            db,
            actor_id=actor_id,
            action_key=action_key,
            request_id=request_id,
            request_count=request_count,
            max_requests=max_requests,
        )
    return True


def enforce_admin_action_rate_limit(
    db: Session,
    *,
    actor_id: int,
    action_key: str,
    max_requests: int,
    request_id: str | None = None,
) -> None:
    if not settings.admin_action_rate_limit_enabled:
        return
    if max_requests <= 0:
        return

    if db.bind is None:
        raise RuntimeError("Admin action rate limiting requires a bound session")

    RateLimitSession = sessionmaker(bind=db.bind, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    with RateLimitSession() as rate_db:
        _enforce_with_session(
            rate_db,
            actor_id=actor_id,
            action_key=action_key,
            max_requests=max_requests,
            request_id=request_id,
        )


def _enforce_with_session(
    db: Session,
    *,
    actor_id: int,
    action_key: str,
    max_requests: int,
    request_id: str | None,
) -> None:
    dialect = db.bind.dialect.name if db.bind is not None else None
    if dialect in {"postgresql", "sqlite"}:
        _enforce_with_atomic_upsert(
            db,
            actor_id=actor_id,
            action_key=action_key,
            max_requests=max_requests,
            request_id=request_id,
        )
        return

    now = utc_now()
    window_start = now - timedelta(seconds=settings.admin_action_rate_limit_window_seconds)
    bucket_key = _bucket_key(actor_id=actor_id, action_key=action_key)

    existing = (
        db.query(AdminActionRateLimitBucket)
        .filter(AdminActionRateLimitBucket.bucket_key == bucket_key)
        .order_by(AdminActionRateLimitBucket.id.desc())
        .first()
    )

    existing_window_start = _normalize_for_compare(existing.window_start) if existing is not None else None
    compare_window_start = _normalize_for_compare(window_start)

    if existing is None or existing_window_start is None or existing_window_start < compare_window_start:
        if existing is None:
            db.add(
                AdminActionRateLimitBucket(
                    bucket_key=bucket_key,
                    window_start=now,
                    request_count=1,
                    updated_at=now,
                )
            )
        else:
            existing.window_start = now
            existing.request_count = 1
            existing.updated_at = now
        db.commit()
        return

    request_count = int(existing.request_count or 0)
    if request_count >= max_requests:
        _log_rate_limit_hit(
            db,
            actor_id=actor_id,
            action_key=action_key,
            request_id=request_id,
            request_count=request_count,
            max_requests=max_requests,
        )

    existing.request_count = request_count + 1
    existing.updated_at = now
    db.commit()
