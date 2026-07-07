from __future__ import annotations

import asyncio
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import httpx
from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse, Response
from starlette.websockets import WebSocketDisconnect

from ..services.observability import log_event
from ..webchat_voice_config import load_webchat_voice_runtime_config

router = APIRouter(tags=["webchat-live-voice"])


def _disabled_response() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "WebChat live voice is disabled"})


def _upstream_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _upstream_ws_url(base_url: str, token: str | None, client_query: str) -> str:
    split = urlsplit(base_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update(dict(parse_qsl(client_query, keep_blank_values=True)))
    if token and "token" not in query:
        query["token"] = token
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


@router.get("/webchat/live/health")
async def webchat_live_voice_health() -> Response:
    config = load_webchat_voice_runtime_config()
    if not config.enabled or not config.live_voice_upstream_health_url:
        return _disabled_response()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            upstream = await client.get(
                config.live_voice_upstream_health_url,
                headers=_upstream_headers(config.live_voice_upstream_token),
            )
    except Exception as exc:
        log_event(30, "webchat_live_voice_health_unavailable", error_type=type(exc).__name__)
        return JSONResponse(status_code=503, content={"status": "unavailable", "detail": "live voice upstream unavailable"})
    content_type = upstream.headers.get("content-type", "application/json")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=content_type)


@router.websocket("/webchat/live/ws")
async def webchat_live_voice_ws(websocket: WebSocket) -> None:
    import websockets

    config = load_webchat_voice_runtime_config()
    if not config.enabled or not config.live_voice_upstream_ws_url:
        await websocket.close(code=4403)
        return
    upstream_url = _upstream_ws_url(config.live_voice_upstream_ws_url, config.live_voice_upstream_token, websocket.url.query)
    try:
        upstream = await websockets.connect(
            upstream_url,
            extra_headers=_upstream_headers(config.live_voice_upstream_token),
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
    except Exception as exc:
        log_event(30, "webchat_live_voice_ws_upstream_connect_failed", error_type=type(exc).__name__)
        await websocket.close(code=1013)
        return

    await websocket.accept()
    log_event(20, "webchat_live_voice_ws_connected")

    async def browser_to_runtime() -> None:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                await upstream.send(message["bytes"])
            elif message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("type") == "websocket.disconnect":
                break

    async def runtime_to_browser() -> None:
        async for message in upstream:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)

    try:
        await asyncio.gather(browser_to_runtime(), runtime_to_browser())
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        log_event(20, "webchat_live_voice_ws_disconnected")
