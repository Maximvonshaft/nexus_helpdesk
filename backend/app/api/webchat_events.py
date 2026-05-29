from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import timezone
from urllib.parse import urlparse
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Ticket
from ..schemas import WebchatRealtimeHealthRead
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatEvent
from ..services.permissions import ensure_can_monitor_webchat_realtime, ensure_ticket_visible
from ..services.realtime_broker import webchat_realtime_broker_status
from ..services.webchat_realtime_hub import webchat_realtime_hub
from .deps import get_current_user

router = APIRouter(prefix="/api/webchat", tags=["webchat-events"])
settings = get_settings()
DEFAULT_EVENTS_MAX_WAIT_MS = 5000

PUBLIC_VISITOR_EVENTS_ERROR = {
    "code": "webchat_conversation_not_found_or_invalid_token",
    "message": "webchat conversation not found or invalid visitor token",
}


class EventPage(dict):
    """Dict-shaped event page with legacy list-like behavior for existing tests."""

    def __iter__(self):
        return iter(self["events"])

    def __len__(self) -> int:
        return len(self["events"])

    def __getitem__(self, key):
        if isinstance(key, str):
            return super().__getitem__(key)
        return self["events"][key]

    def __eq__(self, other):
        if isinstance(other, list):
            return self["events"] == other
        return super().__eq__(other)


def _events_max_wait_ms() -> int:
    raw = os.getenv("WEBCHAT_EVENTS_MAX_WAIT_MS", str(DEFAULT_EVENTS_MAX_WAIT_MS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_EVENTS_MAX_WAIT_MS
    return max(0, min(value, DEFAULT_EVENTS_MAX_WAIT_MS))


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


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _raise_public_visitor_events_auth_error() -> None:
    # Deliberately use one response for missing conversation, wrong visitor token,
    # and expired token. The public polling endpoint must not be usable as a
    # conversation-existence oracle.
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PUBLIC_VISITOR_EVENTS_ERROR)


def _validate_public_visitor_conversation(conversation: WebchatConversation | None, token: str | None) -> WebchatConversation:
    if not conversation or not token:
        _raise_public_visitor_events_auth_error()
    if _hash_token(token) != conversation.visitor_token_hash:
        _raise_public_visitor_events_auth_error()
    expires_at = _ensure_aware_utc(getattr(conversation, "visitor_token_expires_at", None))
    now = _ensure_aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        _raise_public_visitor_events_auth_error()
    return conversation


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


def _safe_limit(limit: int) -> int:
    return max(1, min(int(limit or 50), 100))


def _capped_wait_ms(wait_ms: int) -> int:
    return max(0, min(int(wait_ms or 0), _events_max_wait_ms()))


def _recent_event_type_counts(db: Session) -> dict[str, int]:
    rows = db.query(WebchatEvent.event_type).order_by(WebchatEvent.id.desc()).limit(100).all()
    counts: dict[str, int] = {}
    for (event_type,) in rows:
        key = str(event_type or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _realtime_warnings(*, broker_cross_worker_safe: bool, hub_connections: int) -> list[str]:
    active_settings = get_settings()
    warnings: list[str] = []
    if not active_settings.webchat_ws_enabled:
        warnings.append("webchat_ws_disabled")
    if not active_settings.webchat_ws_admin_enabled:
        warnings.append("webchat_ws_admin_disabled")
    if not active_settings.webchat_ws_public_enabled:
        warnings.append("webchat_ws_public_disabled")
    if not broker_cross_worker_safe:
        warnings.append("webchat_ws_broker_not_cross_worker_safe")
    if active_settings.webchat_ws_max_connections > 0 and hub_connections >= active_settings.webchat_ws_max_connections:
        warnings.append("webchat_ws_connection_limit_reached")
    return warnings


def _list_events(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    after_id: int = 0,
    limit: int = 50,
) -> EventPage:
    safe_limit = _safe_limit(limit)
    query = db.query(WebchatEvent).filter(WebchatEvent.id > max(0, int(after_id or 0)))
    if conversation_id is not None:
        query = query.filter(WebchatEvent.conversation_id == conversation_id)
    if ticket_id is not None:
        query = query.filter(WebchatEvent.ticket_id == ticket_id)
    rows = query.order_by(WebchatEvent.id.asc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    return EventPage({
        "events": [_event_read(row) for row in visible],
        "has_more": len(rows) > safe_limit,
    })


def _wait_for_events(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    after_id: int = 0,
    limit: int = 50,
    wait_ms: int = 0,
) -> EventPage:
    max_wait_ms = _capped_wait_ms(wait_ms)
    deadline = time.monotonic() + (max_wait_ms / 1000.0)
    while True:
        result = _list_events(db, conversation_id=conversation_id, ticket_id=ticket_id, after_id=after_id, limit=limit)
        if result["events"] or max_wait_ms <= 0 or time.monotonic() >= deadline:
            result["wait_ms"] = max_wait_ms
            return result
        time.sleep(min(0.25, max(0.05, deadline - time.monotonic())))


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
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
    conversation = _validate_public_visitor_conversation(conversation, resolved_token)
    result = _wait_for_events(db, conversation_id=conversation.id, after_id=after_id, limit=limit, wait_ms=wait_ms)
    events = result["events"]
    return {
        "events": events,
        "last_event_id": events[-1]["id"] if events else after_id,
        "has_more": result["has_more"],
        "wait_ms": result["wait_ms"],
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
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    result = _wait_for_events(db, ticket_id=ticket_id, after_id=after_id, limit=limit, wait_ms=wait_ms)
    events = result["events"]
    return {
        "events": events,
        "last_event_id": events[-1]["id"] if events else after_id,
        "has_more": result["has_more"],
        "wait_ms": result["wait_ms"],
    }


@router.get("/admin/realtime-health", response_model=WebchatRealtimeHealthRead)
async def admin_webchat_realtime_health(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_monitor_webchat_realtime(current_user, db)
    active_settings = get_settings()
    hub_snapshot = await webchat_realtime_hub.snapshot()
    broker = webchat_realtime_broker_status(active_settings.webchat_ws_broker)
    last_event_id = db.query(func.max(WebchatEvent.id)).scalar() or 0
    last_event_at = db.query(func.max(WebchatEvent.created_at)).scalar()
    recent_event_count = db.query(func.count(WebchatEvent.id)).scalar() or 0
    recent_event_count = min(int(recent_event_count), 100)
    return {
        "enabled": active_settings.webchat_ws_enabled,
        "admin_enabled": active_settings.webchat_ws_admin_enabled,
        "public_enabled": active_settings.webchat_ws_public_enabled,
        "ws_path": "/api/webchat/ws",
        "broker": {
            "name": broker.name,
            "durable_replay": broker.durable_replay,
            "cross_worker_safe": broker.cross_worker_safe,
        },
        "hub": hub_snapshot,
        "events": {
            "last_event_id": int(last_event_id),
            "recent_event_count": int(recent_event_count),
            "last_event_at": last_event_at,
            "event_types": _recent_event_type_counts(db),
        },
        "replay_poll_ms": active_settings.webchat_ws_replay_poll_ms,
        "fallback_poll_ms": active_settings.webchat_ws_fallback_poll_ms,
        "heartbeat_ms": active_settings.webchat_ws_heartbeat_ms,
        "hello_timeout_ms": active_settings.webchat_ws_hello_timeout_ms,
        "max_connections": active_settings.webchat_ws_max_connections,
        "max_connections_per_user": active_settings.webchat_ws_max_connections_per_user,
        "warnings": _realtime_warnings(
            broker_cross_worker_safe=broker.cross_worker_safe,
            hub_connections=int(hub_snapshot.get("connections") or 0),
        ),
    }
