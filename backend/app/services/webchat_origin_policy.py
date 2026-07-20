from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response, status


def normalized_allowed_origins(settings: Any) -> set[str]:
    allowed = {item.rstrip("/") for item in settings.webchat_allowed_origins if str(item).strip()}
    if settings.app_env in {"development", "test", "local"}:
        allowed.update({"http://localhost", "http://127.0.0.1"})
    return allowed


def validate_public_origin(request: Request, settings: Any) -> str | None:
    allowed = normalized_allowed_origins(settings)
    origin = request.headers.get("origin")
    if origin:
        if origin.rstrip("/") not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webchat origin is not allowed")
        return origin

    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            if referer_origin in allowed:
                return referer_origin

    if settings.webchat_allow_no_origin or settings.app_env in {"development", "test", "local"}:
        return None
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webchat origin is required")


def validate_websocket_origin(websocket: Any, settings: Any) -> str | None:
    """Validate the browser Origin before accepting a public WebSocket.

    WebSocket handshakes do not carry a reliable Referer, so production
    connections must either provide an allowed Origin or explicitly opt into
    no-Origin clients through the same server-owned policy used by HTTP.
    """

    allowed = normalized_allowed_origins(settings)
    origin = websocket.headers.get("origin")
    if origin:
        if origin.rstrip("/") not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Webchat origin is not allowed",
            )
        return origin
    if settings.webchat_allow_no_origin or settings.app_env in {"development", "test", "local"}:
        return None
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Webchat origin is required",
    )


def public_cors_headers(
    request: Request,
    settings: Any,
    *,
    methods: Iterable[str],
    request_headers: Iterable[str],
) -> dict[str, str]:
    origin = validate_public_origin(request, settings)
    headers = {
        "Access-Control-Allow-Methods": ", ".join(methods),
        "Access-Control-Allow-Headers": ", ".join(request_headers),
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Cache-Control": "no-store",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def set_public_cors(
    response: Response,
    request: Request,
    settings: Any,
    *,
    methods: Iterable[str],
    request_headers: Iterable[str],
) -> None:
    for key, value in public_cors_headers(
        request,
        settings,
        methods=methods,
        request_headers=request_headers,
    ).items():
        response.headers.setdefault(key, value)
