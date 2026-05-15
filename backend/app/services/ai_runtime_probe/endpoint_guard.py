from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


def _allowed_domains() -> set[str]:
    raw = os.getenv("CODEX_AUTH_PROBE_ALLOWED_DOMAINS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _resolve_ips(host: str, port: int) -> set[ipaddress._BaseAddress]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return {ipaddress.ip_address(item[4][0]) for item in infos}


def validate_probe_endpoint(probe_url: str) -> tuple[bool, str | None]:
    parsed = urlparse(probe_url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        return False, "probe_endpoint_must_be_https"
    if parsed.username or parsed.password:
        return False, "probe_endpoint_userinfo_forbidden"
    if not parsed.path or parsed.path == "/":
        return False, "probe_endpoint_path_required"

    host = parsed.hostname.lower()
    allowed_domains = _allowed_domains()
    if allowed_domains and host not in allowed_domains:
        return False, "probe_endpoint_domain_not_allowed"

    try:
        resolved_ips = _resolve_ips(host, parsed.port or 443)
    except Exception:
        return False, "probe_endpoint_resolution_failed"

    for ip in resolved_ips:
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return False, "probe_endpoint_ip_forbidden"
    return True, None
