#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.parse

_PRIVATE_SUFFIX_HINTS = (".internal", ".local", ".tailnet")
_PRIVATE_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def _is_private_ip(host: str) -> bool:
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _classify_host(hostname: str) -> dict:
    host = (hostname or "").lower()
    result = {
        "hostname": host,
        "private_by_name": False,
        "private_by_dns": False,
        "resolved_addresses": [],
        "warnings": [],
    }
    if host in _PRIVATE_HOSTNAMES or host.endswith(_PRIVATE_SUFFIX_HINTS) or "." not in host:
        result["private_by_name"] = True
    try:
        infos = socket.getaddrinfo(host, None)
        addresses = sorted({item[4][0] for item in infos})
        result["resolved_addresses"] = addresses
        if addresses and all(_is_private_ip(address) for address in addresses):
            result["private_by_dns"] = True
        if any(not _is_private_ip(address) for address in addresses):
            result["warnings"].append("hostname_resolves_to_public_address")
    except socket.gaierror:
        # Docker service names or private-only names may not resolve on the probe host.
        result["warnings"].append("hostname_not_resolvable_from_probe_host")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that OpenClaw Gateway URL is private-only")
    parser.add_argument("--responses-url", required=True)
    parser.add_argument("--allow-unresolved-private-name", action="store_true", default=True)
    args = parser.parse_args()

    parsed = urllib.parse.urlparse(args.responses_url)
    report = {
        "responses_url_scheme": parsed.scheme,
        "responses_url_path": parsed.path,
        "hostname": parsed.hostname,
        "port": parsed.port,
        "checks": [],
        "pass": False,
    }
    failures = []
    if parsed.scheme not in {"http", "https"}:
        failures.append("responses_url_scheme_must_be_http_or_https")
    if not parsed.hostname:
        failures.append("responses_url_must_have_hostname")
    if parsed.path.rstrip("/") != "/v1/responses":
        failures.append("responses_url_path_must_be_/v1/responses")

    if parsed.hostname:
        host_check = _classify_host(parsed.hostname)
        report["checks"].append(host_check)
        private_enough = host_check["private_by_name"] or host_check["private_by_dns"]
        public_resolved = any(w == "hostname_resolves_to_public_address" for w in host_check["warnings"])
        if public_resolved:
            failures.append("hostname_resolves_to_public_address")
        if not private_enough:
            failures.append("hostname_does_not_look_private")

    report["failures"] = failures
    report["pass"] = not failures
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
