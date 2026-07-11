from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse, urlunparse

MAX_PROBE_BYTES = 64 * 1024
_FORBIDDEN_PROBE_PATH_PARTS = {
    "send",
    "execute",
    "dispatch-now",
    "publish",
    "delete",
    "remove",
    "create",
    "update",
    "mutate",
    "action",
}


@dataclass(frozen=True)
class ReadOnlyProbeSpec:
    path: str
    endpoint: str
    method: str = "GET"


@dataclass(frozen=True)
class PreparedProbeTarget:
    url: str
    host: str
    port: int
    request_target: str
    resolved_addresses: tuple[str, ...]


Resolver = Callable[[str, int], Sequence[str]]
Executor = Callable[[PreparedProbeTarget, str, Mapping[str, str], float, int], tuple[int, bytes]]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, *, timeout: float) -> None:
        super().__init__(host=host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self.sock = raw_socket
            self._tunnel()
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_failure(path: str, error_code: str, *, permission_granted: bool = False, status_code: int = 0) -> dict[str, Any]:
    return {
        "path": path,
        "method": "GET",
        "permission_granted": permission_granted,
        "status_code": status_code,
        "payload": {},
        "observed_at": _utc_now_iso(),
        "error_code": error_code,
    }


def _normalize_allowed_host(value: str) -> tuple[str, int]:
    text = str(value or "").strip().lower()
    if not text:
        raise ValueError("unsafe_probe_url")
    parsed = urlparse(f"//{text}")
    host = (parsed.hostname or "").rstrip(".")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("unsafe_probe_url") from exc
    if (
        not host
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("unsafe_probe_url")
    return host, port


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global) and not any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("unsafe_probe_url") from exc
    addresses: list[str] = []
    for record in records:
        raw = record[4][0]
        try:
            normalized = str(ipaddress.ip_address(raw))
        except ValueError:
            continue
        if not _is_public_address(normalized):
            raise ValueError("unsafe_probe_url")
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise ValueError("unsafe_probe_url")
    return tuple(addresses)


def validate_read_only_probe_url(base_url: str, endpoint: str, *, allowed_hosts: Sequence[str]) -> str:
    parsed_base = urlparse(str(base_url or ""))
    host = (parsed_base.hostname or "").lower().rstrip(".")
    try:
        port = parsed_base.port or 443
    except ValueError as exc:
        raise ValueError("unsafe_probe_url") from exc
    if (
        parsed_base.scheme.lower() != "https"
        or not host
        or parsed_base.username
        or parsed_base.password
        or parsed_base.query
        or parsed_base.fragment
        or parsed_base.path not in {"", "/"}
    ):
        raise ValueError("unsafe_probe_url")
    allowed = {_normalize_allowed_host(item) for item in allowed_hosts}
    if (host, port) not in allowed:
        raise ValueError("unsafe_probe_url")

    parsed_endpoint = urlparse(str(endpoint or ""))
    if parsed_endpoint.scheme or parsed_endpoint.netloc or parsed_endpoint.fragment:
        raise ValueError("unsafe_probe_url")
    path = parsed_endpoint.path
    if (
        not path.startswith("/")
        or "//" in path
        or "\\" in path
        or "%" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise ValueError("unsafe_probe_url")
    path_parts = {part.lower() for part in path.split("/") if part}
    if path_parts & {".", ".."}:
        raise ValueError("unsafe_probe_url")
    if path_parts & _FORBIDDEN_PROBE_PATH_PARTS:
        raise ValueError("unsafe_probe_url")

    try:
        is_ipv6_literal = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_ipv6_literal = False
    display_host = f"[{host}]" if is_ipv6_literal else host
    netloc = display_host if port == 443 else f"{display_host}:{port}"
    return urlunparse(("https", netloc, parsed_endpoint.path, "", parsed_endpoint.query, ""))


def prepare_read_only_probe_target(
    base_url: str,
    endpoint: str,
    *,
    allowed_hosts: Sequence[str],
    resolver: Resolver = resolve_public_addresses,
) -> PreparedProbeTarget:
    url = validate_read_only_probe_url(base_url, endpoint, allowed_hosts=allowed_hosts)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port or 443
    try:
        literal_host = str(ipaddress.ip_address(host))
    except ValueError:
        literal_host = None
    if literal_host is not None and not _is_public_address(literal_host):
        raise ValueError("unsafe_probe_url")
    addresses = tuple(resolver(host, port))
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ValueError("unsafe_probe_url")
    if literal_host is not None and set(addresses) != {literal_host}:
        raise ValueError("unsafe_probe_url")
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    return PreparedProbeTarget(
        url=url,
        host=host,
        port=port,
        request_target=request_target,
        resolved_addresses=addresses,
    )


def _execute_pinned_https_get(
    target: PreparedProbeTarget,
    address: str,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
) -> tuple[int, bytes]:
    connection = _PinnedHTTPSConnection(target.host, target.port, address, timeout=timeout)
    try:
        connection.request("GET", target.request_target, body=None, headers=dict(headers))
        response = connection.getresponse()
        body = response.read(max_bytes + 1)
        return int(response.status or 0), body
    finally:
        connection.close()


def run_read_only_http_probe(
    spec: ReadOnlyProbeSpec,
    *,
    base_url: str,
    allowed_hosts: Sequence[str],
    tenant_id: str,
    bearer_token: str,
    timeout_seconds: float = 5.0,
    resolver: Resolver = resolve_public_addresses,
    executor: Executor = _execute_pinned_https_get,
) -> dict[str, Any]:
    if str(spec.method or "").upper() != "GET":
        result = _probe_failure(spec.path, "unsafe_probe_method")
        result["method"] = str(spec.method or "")[:16]
        return result
    try:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError
        timeout = float(timeout_seconds)
        if not 0.1 <= timeout <= 15.0:
            raise ValueError
        target = prepare_read_only_probe_target(
            base_url,
            spec.endpoint,
            allowed_hosts=allowed_hosts,
            resolver=resolver,
        )
    except (TypeError, ValueError, OverflowError):
        return _probe_failure(spec.path, "unsafe_probe_url")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer_token}",
        "Host": (
            f"[{target.host}]" if ":" in target.host else target.host
        ) if target.port == 443 else (
            f"[{target.host}]:{target.port}" if ":" in target.host else f"{target.host}:{target.port}"
        ),
        "X-Nexus-Tenant": tenant_id,
    }
    last_status = 0
    for address in target.resolved_addresses:
        try:
            status_code, body = executor(target, address, headers, timeout, MAX_PROBE_BYTES)
            if isinstance(status_code, bool) or not isinstance(status_code, int):
                raise ValueError("invalid_status")
            if not isinstance(body, bytes):
                raise ValueError("invalid_body")
            last_status = status_code
        except Exception:
            continue
        if 300 <= status_code < 400:
            return _probe_failure(
                spec.path,
                "unsafe_probe_url",
                permission_granted=False,
                status_code=status_code,
            )
        if status_code in {401, 403}:
            return _probe_failure(
                spec.path,
                "permission_denied",
                permission_granted=False,
                status_code=status_code,
            )
        if not 200 <= status_code < 300:
            return _probe_failure(
                spec.path,
                "source_unavailable",
                permission_granted=False,
                status_code=status_code,
            )
        if len(body) > MAX_PROBE_BYTES:
            return _probe_failure(
                spec.path,
                "probe_response_too_large",
                permission_granted=True,
                status_code=status_code,
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return _probe_failure(
                spec.path,
                "payload_invalid",
                permission_granted=True,
                status_code=status_code,
            )
        if not isinstance(payload, Mapping):
            return _probe_failure(
                spec.path,
                "payload_invalid",
                permission_granted=True,
                status_code=status_code,
            )
        return {
            "path": spec.path,
            "method": "GET",
            "permission_granted": True,
            "status_code": status_code,
            "payload": dict(payload),
            "observed_at": _utc_now_iso(),
        }
    return _probe_failure(
        spec.path,
        "source_unavailable",
        permission_granted=False,
        status_code=last_status,
    )
