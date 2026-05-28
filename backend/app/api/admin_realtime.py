from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import RealtimeHealthRead
from ..services.observability import webchat_websocket_observability_snapshot
from ..services.permissions import ensure_can_manage_runtime
from ..services.realtime_broker import webchat_realtime_broker_status
from ..services.webchat_realtime_hub import webchat_realtime_hub
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatEvent
from .deps import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin-realtime"])


@router.get("/realtime-health", response_model=RealtimeHealthRead)
async def realtime_health(db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> RealtimeHealthRead:
    ensure_can_manage_runtime(current_user, db)
    settings = get_settings()
    broker = webchat_realtime_broker_status(settings.webchat_ws_broker)
    connections = await webchat_realtime_hub.snapshot()
    observability = webchat_websocket_observability_snapshot()
    since = utc_now() - timedelta(minutes=5)
    last_event_id, last_event_at = db.query(func.max(WebchatEvent.id), func.max(WebchatEvent.created_at)).one()
    recent_query = db.query(WebchatEvent).filter(WebchatEvent.created_at >= since)
    events_last_5m = recent_query.count()
    handoff_events_last_5m = recent_query.filter(WebchatEvent.event_type.like("handoff.%")).count()
    conversation_events_last_5m = recent_query.filter(~WebchatEvent.event_type.like("handoff.%")).count()

    warnings: list[str] = []
    if not settings.webchat_ws_enabled:
        warnings.append("WebChat WebSocket runtime is disabled; operator UI must rely on polling fallback")
    if settings.webchat_ws_enabled and not settings.webchat_ws_admin_enabled:
        warnings.append("Admin WebSocket runtime is disabled; agent inbox cannot subscribe to realtime queues")
    if settings.webchat_ws_enabled and not settings.webchat_ws_public_enabled:
        warnings.append("Public WebSocket runtime is disabled; visitors use fallback polling")
    if not broker.cross_worker_safe:
        warnings.append("Realtime broker is not cross-worker safe; use database broker before production")
    if int(connections.get("connections", 0)) >= settings.webchat_ws_max_connections:
        warnings.append("WebSocket connection limit is saturated")

    if not settings.webchat_ws_enabled:
        status = "disabled"
    elif warnings:
        status = "degraded"
    else:
        status = "ready"

    return RealtimeHealthRead(
        status=status,
        features={
            "enabled": bool(settings.webchat_ws_enabled),
            "admin_enabled": bool(settings.webchat_ws_admin_enabled),
            "public_enabled": bool(settings.webchat_ws_public_enabled),
            "broker": broker.name,
            "broker_durable_replay": broker.durable_replay,
            "broker_cross_worker_safe": broker.cross_worker_safe,
            "replay_poll_ms": settings.webchat_ws_replay_poll_ms,
            "fallback_poll_ms": settings.webchat_ws_fallback_poll_ms,
            "heartbeat_ms": settings.webchat_ws_heartbeat_ms,
            "hello_timeout_ms": settings.webchat_ws_hello_timeout_ms,
            "max_connections": settings.webchat_ws_max_connections,
            "max_connections_per_user": settings.webchat_ws_max_connections_per_user,
        },
        connections={
            "connections": int(connections.get("connections", 0)),
            "agents": int(connections.get("agents", 0)),
            "visitors": int(connections.get("visitors", 0)),
            "subscriptions": int(connections.get("subscriptions", 0)),
        },
        replay={
            "last_event_id": last_event_id,
            "last_event_at": last_event_at,
            "events_last_5m": events_last_5m,
            "handoff_events_last_5m": handoff_events_last_5m,
            "conversation_events_last_5m": conversation_events_last_5m,
        },
        observability=observability,
        warnings=warnings,
    )
