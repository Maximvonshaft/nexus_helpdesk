from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .observability import log_event
from .whatsapp_embedded_signup_settings import (
    get_whatsapp_embedded_signup_settings,
)


_CHANNEL_PATHS = {"/channels", "/channels/"}
_REQUIRED_SOURCES: dict[str, tuple[str, ...]] = {
    "script-src": ("https://connect.facebook.net",),
    "connect-src": (
        "https://graph.facebook.com",
        "https://www.facebook.com",
    ),
    "frame-src": (
        "https://www.facebook.com",
        "https://web.facebook.com",
    ),
}
_APP_STATE_KEY = "whatsapp_embedded_signup_csp_registered"


def augment_embedded_signup_csp(value: str) -> str:
    directives: list[tuple[str, list[str]]] = []
    positions: dict[str, int] = {}
    for raw in str(value or "").split(";"):
        tokens = raw.strip().split()
        if not tokens:
            continue
        name = tokens[0].lower()
        if name in positions:
            continue
        positions[name] = len(directives)
        directives.append((name, tokens[1:]))
    if "default-src" not in positions:
        positions["default-src"] = len(directives)
        directives.append(("default-src", ["'self'"]))
    for name, required in _REQUIRED_SOURCES.items():
        if name in positions:
            index = positions[name]
            existing = directives[index][1]
            merged = [*existing]
            for source in required:
                if source not in merged:
                    merged.append(source)
            directives[index] = (name, merged)
        else:
            positions[name] = len(directives)
            directives.append((name, list(required)))
    return "; ".join(
        " ".join((name, *sources)).strip()
        for name, sources in directives
    )


def embedded_signup_csp_enabled(path: str) -> bool:
    if path not in _CHANNEL_PATHS:
        return False
    try:
        return bool(get_whatsapp_embedded_signup_settings().enabled)
    except RuntimeError as exc:
        log_event(
            40,
            "whatsapp_embedded_signup_csp_config_invalid",
            error_type=type(exc).__name__,
        )
        return False


class WhatsAppEmbeddedSignupCspMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if embedded_signup_csp_enabled(request.url.path):
            response.headers["Content-Security-Policy"] = augment_embedded_signup_csp(
                response.headers.get("Content-Security-Policy", "")
            )
        return response


def register_whatsapp_embedded_signup_csp(app: FastAPI) -> None:
    if bool(getattr(app.state, _APP_STATE_KEY, False)):
        return
    app.add_middleware(WhatsAppEmbeddedSignupCspMiddleware)
    setattr(app.state, _APP_STATE_KEY, True)
