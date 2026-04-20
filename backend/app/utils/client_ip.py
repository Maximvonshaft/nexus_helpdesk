from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Iterable

from fastapi import Request

from ..settings import get_settings


@lru_cache(maxsize=32)
def _parse_trusted_network(raw: str):
    try:
        if '/' in raw:
            return ipaddress.ip_network(raw, strict=False)
        return ipaddress.ip_network(raw + ('/128' if ':' in raw else '/32'), strict=False)
    except ValueError:
        return None


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return candidate


def _is_trusted_proxy(remote_addr: str | None, trusted_proxy_ips: Iterable[str]) -> bool:
    normalized = _normalize_ip(remote_addr)
    if not normalized:
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    for raw in trusted_proxy_ips:
        network = _parse_trusted_network(raw.strip())
        if network and address in network:
            return True
    return False


def _extract_forwarded_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get('x-forwarded-for')
    if forwarded_for:
        for part in forwarded_for.split(','):
            normalized = _normalize_ip(part)
            if normalized:
                return normalized
    return _normalize_ip(request.headers.get('x-real-ip'))


def get_client_ip(request: Request) -> str:
    settings = get_settings()
    remote_addr = _normalize_ip(request.client.host if request.client else None)
    if _is_trusted_proxy(remote_addr, settings.trusted_proxy_ips):
        forwarded = _extract_forwarded_ip(request)
        if forwarded:
            return forwarded
    return remote_addr or 'unknown'
