#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_HEADER_BYTES = 65536


@dataclass(frozen=True)
class WebSocketUpgradeResult:
    status_code: int
    request_path: str
    upgrade_header: str
    connection_header: str


def _request_target(base_url: str, path: str, query: str) -> tuple[SplitResult, str, str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute http:// or https:// URL")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query string or fragment")

    normalized_path = "/" + path.lstrip("/")
    base_path = parsed.path.rstrip("/")
    request_path = f"{base_path}{normalized_path}" or "/"
    clean_query = query.lstrip("?")
    target = f"{request_path}?{clean_query}" if clean_query else request_path
    return parsed, request_path, target


def _host_header(parsed: SplitResult) -> tuple[str, str, int]:
    host = parsed.hostname
    if not host:
        raise ValueError("base URL hostname is required")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host_literal = f"[{host}]" if ":" in host else host
    host_header = host_literal if parsed.port is None else f"{host_literal}:{port}"
    return host_header, host, port


def _secure_ssl_context(context: ssl.SSLContext | None = None) -> ssl.SSLContext:
    try:
        tls_v1_2 = ssl.TLSVersion.TLSv1_2
    except AttributeError as exc:
        raise RuntimeError("TLS 1.2 support is required for HTTPS websocket probes") from exc

    if context is None:
        secure_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        secure_context.check_hostname = True
        secure_context.verify_mode = ssl.CERT_REQUIRED
        secure_context.load_default_certs()
        secure_context.minimum_version = tls_v1_2
    else:
        secure_context = context
        try:
            if secure_context.minimum_version < tls_v1_2:
                secure_context.minimum_version = tls_v1_2
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("unable to enforce TLS 1.2 minimum for HTTPS websocket probe") from exc
    if secure_context.minimum_version < tls_v1_2:
        raise RuntimeError("HTTPS websocket probe TLS minimum is below TLS 1.2")
    return secure_context


def _read_headers(connection: socket.socket) -> bytes:
    response = b""
    while b"\r\n\r\n" not in response and len(response) < _MAX_HEADER_BYTES:
        chunk = connection.recv(4096)
        if not chunk:
            break
        response += chunk
    header_bytes, separator, _ = response.partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError("websocket probe returned incomplete HTTP headers")
    return header_bytes


def _parse_headers(header_bytes: bytes) -> tuple[int, dict[str, str]]:
    lines = header_bytes.decode("iso-8859-1").split("\r\n")
    status_parts = lines[0].split(" ", 2)
    try:
        status_code = int(status_parts[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("websocket probe returned an invalid HTTP status line") from exc

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        normalized_name = name.strip().lower()
        normalized_value = value.strip()
        if normalized_name == "connection" and normalized_name in headers:
            headers[normalized_name] = f"{headers[normalized_name]}, {normalized_value}"
        else:
            headers[normalized_name] = normalized_value
    return status_code, headers


def _header_tokens(value: str) -> set[str]:
    return {token.strip().lower() for token in value.split(",") if token.strip()}


def probe_websocket_upgrade(
    *,
    base_url: str,
    query: str = "",
    timeout_seconds: float = 10.0,
    ssl_context: ssl.SSLContext | None = None,
) -> WebSocketUpgradeResult:
    parsed, request_path, target = _request_target(base_url, path, query)
    host_header, host, port = _host_header(parsed)
    websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
    origin = f"{parsed.scheme}://{host_header}"
    request = "\r\n".join(
        [
            f"GET {target} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {websocket_key}",
            "Sec-WebSocket-Version: 13",
            f"Origin: {origin}",
            "",
            "",
        ]
    ).encode("ascii")

    raw_socket: socket.socket | None = None
    connection: socket.socket | None = None
    try:
        raw_socket = socket.create_connection((host, port), timeout=timeout_seconds)
        connection = raw_socket
        if parsed.scheme == "https":
            context = _secure_ssl_context(ssl_context)
            connection = context.wrap_socket(raw_socket, server_hostname=host)
        connection.settimeout(timeout_seconds)
        connection.sendall(request)
        header_bytes = _read_headers(connection)
    finally:
        if connection is not None:
            connection.close()
        elif raw_socket is not None:
            raw_socket.close()

    status_code, headers = _parse_headers(header_bytes)
    upgrade_header = headers.get("upgrade", "")
    connection_header = headers.get("connection", "")
    if status_code != 101:
        raise RuntimeError(f"websocket upgrade failed with HTTP {status_code}")
    if upgrade_header.lower() != "websocket":
        raise RuntimeError("websocket Upgrade response header is invalid")
    if "upgrade" not in _header_tokens(connection_header):
        raise RuntimeError("websocket Connection response header is invalid")

    expected_accept = base64.b64encode(
        hashlib.sha1((websocket_key + _WEBSOCKET_GUID).encode("ascii")).digest()
    ).decode("ascii")
    actual_accept = headers.get("sec-websocket-accept", "")
    if not hmac.compare_digest(actual_accept, expected_accept):
        raise RuntimeError("websocket Sec-WebSocket-Accept validation failed")

    return WebSocketUpgradeResult(
        status_code=status_code,
        request_path=request_path,
        upgrade_header=upgrade_header,
        connection_header=connection_header,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an HTTP WebSocket 101 upgrade without external dependencies."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = probe_websocket_upgrade(
            base_url=args.base_url,
            path=args.path,
            query=args.query,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print("LIVE_VOICE_WS_UPGRADE_PASS=false")
        print(f"error_type={type(exc).__name__}")
        print(f"error={exc}")
        return 2

    print("LIVE_VOICE_WS_UPGRADE_PASS=true")
    print(f"status={result.status_code}")
    print(f"path={result.request_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
