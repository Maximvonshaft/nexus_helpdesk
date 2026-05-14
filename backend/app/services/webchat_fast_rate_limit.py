from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import text

from ..db import db_context
from ..utils.time import utc_now
from .webchat_fast_config import get_webchat_fast_settings


@dataclass(frozen=True)
class FastClientIdentity:
    tenant_key: str
    client_ip: str
    origin: str
    fingerprint: str


_RATE_LIMIT_UPSERT_SQL = text(
    """
    INSERT INTO webchat_rate_limits (bucket_key, window_start, request_count, updated_at)
    VALUES (:bucket_key, :window_start, 1, :updated_at)
    ON CONFLICT(bucket_key) DO UPDATE
    SET
        window_start = CASE
            WHEN webchat_rate_limits.window_start < :window_cutoff THEN excluded.window_start
            ELSE webchat_rate_limits.window_start
        END,
        request_count = CASE
            WHEN webchat_rate_limits.window_start < :window_cutoff THEN 1
            ELSE webchat_rate_limits.request_count + 1
        END,
        updated_at = excluded.updated_at
    WHERE webchat_rate_limits.window_start < :window_cutoff
       OR webchat_rate_limits.request_count < :max_requests
    RETURNING request_count
    """
)


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _is_trusted_proxy(value: str) -> bool:
    settings = get_webchat_fast_settings()
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    for cidr in settings.trusted_proxy_cidrs:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def trusted_client_ip(request: Request) -> str:
    remote = request.client.host if request.client else "unknown"
    settings = get_webchat_fast_settings()
    if settings.rate_limit_trust_x_forwarded_for and remote != "unknown" and _is_trusted_proxy(remote):
        xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
        if xff:
            for candidate in [part.strip() for part in xff.split(",") if part.strip()]:
                if _is_public_ip(candidate):
                    return candidate
    return remote


def _normalized_origin(request: Request) -> str:
    origin = (request.headers.get("origin") or request.headers.get("referer") or "").strip().lower()
    return origin[:255] if origin else "no-origin"


def _client_fingerprint(request: Request) -> str:
    supplied = (request.headers.get("x-webchat-client-fingerprint") or "").strip()
    if supplied:
        return supplied[:255]
    user_agent = (request.headers.get("user-agent") or "unknown-agent").strip()
    return user_agent[:255]


def _bucket_key(identity: FastClientIdentity) -> str:
    return f"fast:{identity.tenant_key or 'default'}:{identity.client_ip}:{identity.origin}:{identity.fingerprint}"


def _cleanup_expired_rows(db, *, now, window_seconds: int) -> None:
    cutoff = now - timedelta(seconds=max(window_seconds * 10, 600))
    db.execute(
        text(
            "DELETE FROM webchat_rate_limits "
            "WHERE bucket_key LIKE 'fast:%' AND updated_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )


def _enforce_database(bucket_key: str, *, window_seconds: int, max_requests: int) -> None:
    now = utc_now()
    window_cutoff = now - timedelta(seconds=window_seconds)
    with db_context() as db:
        row = db.execute(
            _RATE_LIMIT_UPSERT_SQL,
            {
                "bucket_key": bucket_key,
                "window_start": now,
                "updated_at": now,
                "window_cutoff": window_cutoff,
                "max_requests": max_requests,
            },
        ).first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many webchat fast reply requests")
        _cleanup_expired_rows(db, now=now, window_seconds=window_seconds)


def enforce_webchat_fast_rate_limit(request: Request, *, tenant_key: str, session_id: str) -> None:
    settings = get_webchat_fast_settings()
    key = _bucket_key(
        FastClientIdentity(
            tenant_key=tenant_key,
            client_ip=trusted_client_ip(request),
            origin=_normalized_origin(request),
            fingerprint=_client_fingerprint(request),
        )
    )
    _enforce_database(key, window_seconds=settings.rate_limit_window_seconds, max_requests=settings.rate_limit_max_requests)


def reset_webchat_fast_rate_limit_for_tests() -> None:
    get_webchat_fast_settings.cache_clear()
    with db_context() as db:
        db.execute(text("DELETE FROM webchat_rate_limits WHERE bucket_key LIKE 'fast:%'"))
        db.flush()
