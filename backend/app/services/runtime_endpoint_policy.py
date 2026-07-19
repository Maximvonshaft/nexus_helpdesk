from __future__ import annotations

from urllib.parse import urlparse


def safe_url_path(value: str) -> str:
    return urlparse(value or "").path or "/"


def same_runtime_origin(left: str, right: str) -> bool:
    left_parsed = urlparse(left or "")
    right_parsed = urlparse(right or "")
    return (
        left_parsed.scheme.lower(),
        left_parsed.hostname or "",
        left_parsed.port,
    ) == (
        right_parsed.scheme.lower(),
        right_parsed.hostname or "",
        right_parsed.port,
    )


def endpoint_shape_mismatch(
    path: str,
    request_shape: str,
    *,
    code_prefix: str = "",
) -> str | None:
    normalized_path = safe_url_path(path).rstrip("/") or "/"
    mismatch = (
        normalized_path == "/api/chat" and request_shape != "ollama_chat"
    ) or (
        normalized_path in {"/chat/direct", "/chat/rag"} and request_shape != "question"
    )
    return f"{code_prefix}endpoint_request_shape_mismatch" if mismatch else None


def require_http_endpoint(value: str, *, label: str = "runtime endpoint") -> str:
    """Return a normalized HTTP(S) endpoint or fail closed.

    Provider endpoints are configuration-owned, but they must never select file,
    data, ftp, unix or another urllib handler. TLS requirements remain a
    deployment-profile concern because controlled private runtimes may use HTTP
    on an isolated network.
    """

    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} must not embed credentials")
    return candidate
