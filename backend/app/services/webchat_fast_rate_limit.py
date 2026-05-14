from __future__ import annotations

import hashlib
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


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_public_ip(value: str) -> bool:
    ip = _parse_ip(value)
    if ip is None:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _is_trusted_proxy(value: str) -> bool:
    settings = get_webchat_fast_settings()
    ip = _parse_ip(value)
    if ip is None:
        return False
    for cidr in settings.trusted_proxy_cidrs:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_ip_from_forwarded_for(xff: str) -> str | None:
    """Return the right-most untrusted public client from an X-Forwarded-For chain.

    With proxy_add_x_forwarded_for, spoofed client-supplied values can be
    preserved on the left while the trusted proxy appends the direct peer on the
    right. Walking from right to left and skipping trusted proxies prevents
    attackers from rotating rate-limit buckets by changing the left-most value.
    """

    candidates = [part.strip() for part in (xff or "").split(",") if part.strip()]
    for candidate in reversed(candidates):
        if _is_public_ip(candidate) and not _is_trusted_proxy(candidate):
            return candidate
    return None


def trusted_client_ip(request: Request) -> str:
    remote = request.client.host if request.client else "unknown"
    settings = get_webchat_fast_settings()
    if settings.rate_limit_trust_x_forwarded_for and remote != "unknown" and _is_trusted_proxy(remote):
        xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
        if xff:
            forwarded_client = _client_ip_from_forwarded_for(xff)
            if forwarded_client:
                return forwarded_client
    return remote


def _normalized_origin(request: Request) -> str:
    origin = (request.headers.get("origin") or request.headers.get("referer") or "").strip().lower()
    return origin or "no-origin"


def _client_fingerprint(request: Request) -> str:
    supplied = (request.headers.get("x-webchat-client-fingerprint") or "").strip()
    if supplied:
        return supplied
    user_agent = (request.headers.get("user-agent") or "unknown-agent").strip()
    return user_agent


def _bucket_key(identity: FastClientIdentity) -> str:
    raw_identity = "|".join((identity.tenant_key or "default", identity.client_ip, identity.origin, identity.fingerprint))
    return hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()


def _cleanup_expired_rows(db, *, now, window_seconds: int) -> None:
    cutoff = now - timedelta(seconds=max(window_seconds * 10, 600))
    db.execute(
        text("DELETE FROM webchat_rate_limits WHERE updated_at < :cutoff"),
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
        db.execute(text("DELETE FROM webchat_rate_limits"))
        db.flush()
