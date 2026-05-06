from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlparse
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..settings import get_settings
from ..webchat_models import WebchatConversation, WebchatEvent
from .deps import get_current_user

router = APIRouter(prefix="/api/webchat", tags=["webchat-events"])
settings = get_settings()


def _normalized_allowed_origins() -> set[str]:
    allowed = {item.rstrip("/") for item in settings.webchat_allowed_origins if item.strip()}
    if settings.app_env in {"development", "test", "local"}:
        allowed.update({"http://localhost", "http://127.0.0.1"})
    return allowed


def _validated_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    allowed = _normalized_allowed_origins()
    if origin:
        normalized = origin.rstrip("/")
        if normalized not in allowed:
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


def _public_cors_headers(request: Request) -> dict[str, str]:
    origin = _validated_origin(request)
    headers = {
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Requested-With, X-Webchat-Visitor-Token",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Cache-Control": "no-store",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _set_public_cors(response: Response, request: Request) -> None:
    for key, value in _public_cors_headers(request).items():
        response.headers.setdefault(key, value)


def _legacy_token_transport_enabled() -> bool:
    return settings.webchat_allow_legacy_token_transport


def _resolve_visitor_token(header_token: str | None, query_token: str | None) -> str | None:
    if header_token:
        return header_token
    if _legacy_token_transport_enabled():
        return query_token
    return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _event_read(row: WebchatEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "payload_json": _loads_json(row.payload_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _list_events(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    after_id: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 100))
    query = db.query(WebchatEvent).filter(WebchatEvent.id > max(0, int(after_id or 0)))
    if conversation_id is not None:
        query = query.filter(WebchatEvent.conversation_id == conversation_id)
    if ticket_id is not None:
        query = query.filter(WebchatEvent.ticket_id == ticket_id)
    rows = query.order_by(WebchatEvent.id.asc()).limit(safe_limit).all()
    return [_event_read(row) for row in rows]


def _wait_for_events(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    after_id: int = 0,
    limit: int = 50,
    wait_ms: int = 0,
) -> list[dict[str, Any]]:
    max_wait_ms = max(0, min(int(wait_ms or 0), 25000))
    deadline = time.monotonic() + (max_wait_ms / 1000.0)
    while True:
        events = _list_events(db, conversation_id=conversation_id, ticket_id=ticket_id, after_id=after_id, limit=limit)
        if events or max_wait_ms <= 0 or time.monotonic() >= deadline:
            return events
        time.sleep(min(0.5, max(0.05, deadline - time.monotonic())))


@router.options("/conversations/{conversation_id}/events")
def webchat_events_options(conversation_id: str, request: Request):
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.get("/conversations/{conversation_id}/events")
def poll_webchat_events(
    conversation_id: str,
    request: Request,
    response: Response,
    visitor_token: str | None = Query(default=None),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    wait_ms: int = Query(default=0, ge=0, le=25000),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    resolved_token = _resolve_visitor_token(x_webchat_visitor_token, visitor_token)
    if not resolved_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    if _hash_token(resolved_token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    events = _wait_for_events(db, conversation_id=conversation.id, after_id=after_id, limit=limit, wait_ms=wait_ms)
    return {
        "events": events,
        "last_event_id": events[-1]["id"] if events else after_id,
        "has_more": len(events) >= min(limit, 100),
        "wait_ms": min(wait_ms, 25000),
    }


@router.get("/admin/tickets/{ticket_id}/events")
def admin_poll_webchat_events(
    ticket_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    wait_ms: int = Query(default=0, ge=0, le=25000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    # Authentication is enforced by get_current_user. This endpoint mirrors the
    # existing WebChat admin thread route and intentionally avoids WebSocket
    # complexity; clients may use it as long-poll or simple after_id polling.
    _ = current_user
    events = _wait_for_events(db, ticket_id=ticket_id, after_id=after_id, limit=limit, wait_ms=wait_ms)
    return {
        "events": events,
        "last_event_id": events[-1]["id"] if events else after_id,
        "has_more": len(events) >= min(limit, 100),
        "wait_ms": min(wait_ms, 25000),
    }
