from __future__ import annotations

import base64
import hashlib
import importlib.util
import socket
import ssl
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "scripts" / "smoke" / "websocket_upgrade_probe.py"


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("nexus_websocket_upgrade_probe", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PROBE = _load_probe_module()


def _request_headers(request: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in request.decode("ascii").split("\r\n")[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def _start_server(response_factory: Callable[[bytes], bytes]):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    capture: dict[str, bytes | BaseException] = {}

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                capture["request"] = request
                connection.sendall(response_factory(request))
        except BaseException as exc:  # pragma: no cover - surfaced by the caller
            capture["error"] = exc
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", capture, thread


def _join_server(capture: dict[str, bytes | BaseException], thread: threading.Thread) -> None:
    thread.join(timeout=2)
    assert not thread.is_alive()
    error = capture.get("error")
    if isinstance(error, BaseException):
        raise error


def _switching_protocols_response(
    request: bytes,
    *,
    accept_override: str | None = None,
    connection_override: str | None = None,
    connection_headers: tuple[str, ...] | None = None,
) -> bytes:
    websocket_key = _request_headers(request)["sec-websocket-key"]
    expected_accept = base64.b64encode(
        hashlib.sha1(
            (websocket_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()
    ).decode("ascii")
    accept = expected_accept if accept_override is None else accept_override
    connection_values = (
        connection_headers
        if connection_headers is not None
        else ("Upgrade" if connection_override is None else connection_override,)
    )
    connection_lines = "".join(f"Connection: {value}\r\n" for value in connection_values)
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        f"{connection_lines}"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")


def test_probe_accepts_valid_websocket_upgrade_and_preserves_base_path():
    base_url, capture, thread = _start_server(_switching_protocols_response)

    result = PROBE.probe_websocket_upgrade(
        base_url=f"{base_url}/candidate",
        query="lang_code=en&voice=bm_george&speed=1.0",
        timeout_seconds=1,
    )
    _join_server(capture, thread)

    request = capture["request"]
    assert isinstance(request, bytes)
    assert request.startswith(
        b"GET /candidate/webchat/live/ws?lang_code=en&voice=bm_george&speed=1.0 HTTP/1.1\r\n"
    )
    assert b"Upgrade: websocket\r\n" in request
    assert b"Connection: Upgrade\r\n" in request
    assert b"Sec-WebSocket-Version: 13\r\n" in request
    assert result.status_code == 101
    assert result.request_path == "/candidate/webchat/live/ws"


@pytest.mark.parametrize(
    "connection_headers",
    [
        ("Upgrade", "keep-alive"),
        ("keep-alive", "Upgrade"),
    ],
)
def test_probe_accepts_repeated_connection_headers_in_any_order(
    connection_headers: tuple[str, ...],
):
    base_url, capture, thread = _start_server(
        lambda request: _switching_protocols_response(
            request,
            connection_headers=connection_headers,
        )
    )

    result = PROBE.probe_websocket_upgrade(base_url=base_url, timeout_seconds=1)
    _join_server(capture, thread)

    assert result.connection_header == ", ".join(connection_headers)


def test_secure_ssl_context_enforces_tls_1_2_minimum():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED

    secured = PROBE._secure_ssl_context(context)

    assert secured is context
    assert secured.minimum_version == ssl.TLSVersion.TLSv1_2


def test_secure_ssl_context_preserves_stricter_tls_minimum():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3

    secured = PROBE._secure_ssl_context(context)

    assert secured.minimum_version == ssl.TLSVersion.TLSv1_3


def test_secure_ssl_context_hardens_default_context(monkeypatch: pytest.MonkeyPatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    monkeypatch.setattr(PROBE.ssl, "create_default_context", lambda: context)

    secured = PROBE._secure_ssl_context()

    assert secured.minimum_version == ssl.TLSVersion.TLSv1_2


def test_secure_ssl_context_fails_closed_without_tls_version_support(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delattr(PROBE.ssl, "TLSVersion")

    with pytest.raises(RuntimeError, match="TLS 1.2 support is required"):
        PROBE._secure_ssl_context()


def test_probe_rejects_non_switching_http_status():
    def forbidden_response(_: bytes) -> bytes:
        return b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"

    base_url, capture, thread = _start_server(forbidden_response)

    with pytest.raises(RuntimeError, match="HTTP 403"):
        PROBE.probe_websocket_upgrade(base_url=base_url, timeout_seconds=1)
    _join_server(capture, thread)


def test_probe_rejects_invalid_websocket_accept_header():
    base_url, capture, thread = _start_server(
        lambda request: _switching_protocols_response(request, accept_override="invalid")
    )

    with pytest.raises(RuntimeError, match="Sec-WebSocket-Accept"):
        PROBE.probe_websocket_upgrade(base_url=base_url, timeout_seconds=1)
    _join_server(capture, thread)


def test_probe_rejects_connection_header_with_upgrade_substring_only():
    base_url, capture, thread = _start_server(
        lambda request: _switching_protocols_response(
            request,
            connection_override="keep-alive, x-upgrade",
        )
    )

    with pytest.raises(RuntimeError, match="Connection response header"):
        PROBE.probe_websocket_upgrade(base_url=base_url, timeout_seconds=1)
    _join_server(capture, thread)


def test_probe_rejects_repeated_connection_headers_without_exact_upgrade():
    base_url, capture, thread = _start_server(
        lambda request: _switching_protocols_response(
            request,
            connection_headers=("keep-alive", "x-upgrade"),
        )
    )

    with pytest.raises(RuntimeError, match="Connection response header"):
        PROBE.probe_websocket_upgrade(base_url=base_url, timeout_seconds=1)
    _join_server(capture, thread)


def test_probe_rejects_ambiguous_base_url_query():
    with pytest.raises(ValueError, match="query string or fragment"):
        PROBE.probe_websocket_upgrade(base_url="http://127.0.0.1:1?mode=ambiguous")
