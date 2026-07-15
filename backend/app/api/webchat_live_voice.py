from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from ..db import SessionLocal, get_db
from ..unit_of_work import managed_session
from ..services.live_voice_orchestration_service import (
    authorize_runtime_voice_socket,
    create_runtime_voice_session,
    end_runtime_voice_session,
    process_runtime_voice_turn,
)
from ..services.observability import log_event
from ..webchat_voice_config import load_webchat_voice_runtime_config

router = APIRouter(tags=["webchat-live-voice"])


class LiveVoiceSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: str | None = Field(default=None, max_length=20)


class LiveVoiceTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=64)
    voice_session_id: str = Field(min_length=1, max_length=64)
    turn_id: int = Field(ge=1, le=1_000_000)
    transcript: str = Field(min_length=1, max_length=2000)
    stt_language: str | None = Field(default=None, max_length=20)


def _disabled_response() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "WebChat live voice is disabled"})


def _upstream_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _upstream_ws_url(base_url: str, token: str | None, parameters: dict[str, str]) -> str:
    split = urlsplit(base_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update(parameters)
    if token and "token" not in query:
        query["token"] = token
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _connection_ticket(*, secret: str, conversation_id: str, voice_session_id: str, expires_at: int) -> str:
    payload = f"{conversation_id}|{voice_session_id}|{expires_at}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _valid_connection_ticket(
    *,
    ticket: str,
    secret: str,
    conversation_id: str,
    voice_session_id: str,
) -> bool:
    try:
        encoded, supplied_signature = ticket.split(".", 1)
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding)
        decoded_conversation, decoded_session, raw_expiry = payload.decode("utf-8").split("|", 2)
        expected_signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return bool(
            hmac.compare_digest(supplied_signature, expected_signature)
            and decoded_conversation == conversation_id
            and decoded_session == voice_session_id
            and int(raw_expiry) >= int(time.time())
        )
    except (ValueError, TypeError, UnicodeDecodeError):
        return False


def _shared_token_from_authorization(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


@router.post("/api/webchat/conversations/{conversation_id}/live-voice/session")
def create_live_voice_session(
    conversation_id: str,
    payload: LiveVoiceSessionRequest,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
) -> dict[str, object]:
    config = load_webchat_voice_runtime_config()
    if not config.enabled or not config.live_voice_upstream_token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="live voice is unavailable")
    with managed_session(db):
        voice_session = create_runtime_voice_session(
            db,
            conversation_public_id=conversation_id,
            visitor_token=x_webchat_visitor_token,
            locale=payload.locale,
            ttl_seconds=config.session_ttl_seconds,
        )
        expires_at = int(time.time()) + min(config.session_ttl_seconds, 300)
        return {
            "voice_session_id": voice_session.public_id,
            "connection_ticket": _connection_ticket(
                secret=config.live_voice_upstream_token,
                conversation_id=conversation_id,
                voice_session_id=voice_session.public_id,
                expires_at=expires_at,
            ),
            "expires_at": expires_at,
        }


@router.post("/api/internal/live-voice/turn")
def process_live_voice_turn(
    payload: LiveVoiceTurnRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = load_webchat_voice_runtime_config()
    supplied_token = _shared_token_from_authorization(authorization)
    expected_token = config.live_voice_upstream_token or ""
    if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid live voice service credential")
    with managed_session(db):
        return process_runtime_voice_turn(
            db,
            conversation_public_id=payload.conversation_id,
            voice_session_public_id=payload.voice_session_id,
            turn_id=payload.turn_id,
            transcript=payload.transcript,
            stt_language=payload.stt_language,
        )


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
    if not config.enabled or not config.live_voice_upstream_ws_url or not config.live_voice_upstream_token:
        await websocket.close(code=4403)
        return
    conversation_id = str(websocket.query_params.get("conversation_id") or "").strip()
    voice_session_id = str(websocket.query_params.get("voice_session_id") or "").strip()
    connection_ticket = str(websocket.query_params.get("connection_ticket") or "").strip()
    if not _valid_connection_ticket(
        ticket=connection_ticket,
        secret=config.live_voice_upstream_token,
        conversation_id=conversation_id,
        voice_session_id=voice_session_id,
    ):
        await websocket.close(code=4403)
        return

    try:
        with SessionLocal() as db:
            with managed_session(db):
                authorize_runtime_voice_socket(
                    db,
                    conversation_public_id=conversation_id,
                    voice_session_public_id=voice_session_id,
                )
    except HTTPException:
        await websocket.close(code=4403)
        return

    upstream_url = _upstream_ws_url(
        config.live_voice_upstream_ws_url,
        config.live_voice_upstream_token,
        {
            "conversation_id": conversation_id,
            "voice_session_id": voice_session_id,
            "lang_code": str(websocket.query_params.get("lang_code") or "auto")[:20],
            "voice": str(websocket.query_params.get("voice") or "")[:80],
            "speed": str(websocket.query_params.get("speed") or "1.0")[:10],
        },
    )
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
        try:
            with SessionLocal() as db:
                with managed_session(db):
                    end_runtime_voice_session(db, voice_session_public_id=voice_session_id, reason="upstream_unavailable")
        except Exception as end_exc:
            log_event(30, "webchat_live_voice_session_end_failed", error_type=type(end_exc).__name__)
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
        try:
            with SessionLocal() as db:
                with managed_session(db):
                    end_runtime_voice_session(db, voice_session_public_id=voice_session_id, reason="socket_disconnected")
        except Exception as exc:
            log_event(30, "webchat_live_voice_session_end_failed", error_type=type(exc).__name__)
        log_event(20, "webchat_live_voice_ws_disconnected")
