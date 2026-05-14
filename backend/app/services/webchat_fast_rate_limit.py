from __future__ import annotations

import ipaddress
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

    Nginx commonly uses proxy_add_x_forwarded_for, so a client-supplied spoofed
    XFF value can be preserved on the left while the real peer seen by Nginx is
    appended on the right. Choosing the first public address lets attackers
    rotate rate-limit buckets by spoofing the left-most value. Walking from
    right to left and skipping trusted proxies binds the bucket to the closest
    untrusted public client.
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


def _bucket_key(identity: FastClientIdentity) -> str:
    return f"{identity.tenant_key or 'default'}:{identity.session_id}:{identity.client_ip}"


def enforce_webchat_fast_rate_limit(request: Request, *, tenant_key: str, session_id: str) -> None:
    settings = get_webchat_fast_settings()
    now = time.time()
    key = _bucket_key(FastClientIdentity(tenant_key=tenant_key, session_id=session_id, client_ip=trusted_client_ip(request)))
    bucket = [ts for ts in _BUCKETS.get(key, []) if now - ts < settings.rate_limit_window_seconds]
    if len(bucket) >= settings.rate_limit_max_requests:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many webchat fast reply requests")
    bucket.append(now)
    _BUCKETS[key] = bucket


def reset_webchat_fast_rate_limit_for_tests() -> None:
    _BUCKETS.clear()
