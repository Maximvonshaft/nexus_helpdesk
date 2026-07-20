from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from ..auth_service import decode_access_token
from ..db import get_db
from ..models import User
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..services.permissions import (
    ensure_can_accept_webchat_handoff,
    ensure_can_monitor_webchat_ai,
    ensure_can_send_outbound,
)
from ..services.observability import (
    log_event,
    record_webchat_websocket_auth_failed,
    record_webchat_websocket_connected,
    record_webchat_websocket_disconnected,
    record_webchat_websocket_event_replay,
    record_webchat_websocket_event_sent,
    record_webchat_websocket_fallback_polling,
)
from ..services.webchat_handoff_service import force_takeover_ticket, list_handoff_queue
from ..services.webchat_realtime_event_service import (
    list_admin_queue_event_envelopes,
    list_conversation_event_envelopes,
    validate_agent_conversation,
    validate_visitor_conversation,
)
from ..services.webchat_realtime_hub import webchat_realtime_hub
from ..services.webchat_service import admin_reply

router = APIRouter(tags=["webchat-websocket"])


@dataclass
class ConversationSubscription:
    conversation_id: int
    public_id: str
    ticket_id: int
    audience: str
    last_event_id: int = 0


@dataclass
class QueueSubscription:
    view: str
    last_event_id: int = 0


@dataclass
class ConnectionState:
    connection_id: str | None = None
    client_type: str | None = None
    current_user: User | None = None
    visitor_token: str | None = None
    conversations: list[ConversationSubscription] | None = None
    queues: list[QueueSubscription] | None = None
    hub_version: int = 0

    def __post_init__(self) -> None:
        self.conversations = []
        self.queues = []


def _access_token_from_headers(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


def _load_user(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    user_id = decode_access_token(token)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()


def _error_payload(code: str, message: str, *, retryable: bool = False, request_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "error", "code": code, "message": message, "retryable": retryable}
    if request_id:
        payload["request_id"] = request_id
    return payload


def _record_auth_failed(client_type: str | None, reason: str) -> None:
    safe_client_type = client_type if client_type in {"agent", "visitor"} else "unknown"
    record_webchat_websocket_auth_failed(safe_client_type, reason)
    log_event(30, "websocket_auth_failed", client_type=safe_client_type, reason=reason)


async def _send_error(websocket: WebSocket, code: str, message: str, *, retryable: bool = False, request_id: str | None = None) -> None:
    await websocket.send_json(_error_payload(code, message, retryable=retryable, request_id=request_id))


async def _send_ready(websocket: WebSocket, state: ConnectionState) -> None:
    settings = get_settings()
    assert state.connection_id is not None
    await websocket.send_json(
        {
            "type": "connection.ready",
            "connection_id": state.connection_id,
            "client_type": state.client_type,
            "ws_version": "2026-05-27.webchat.v1",
            "features": {
                "replay": True,
                "handoff_queue": state.client_type == "agent",
                "public_conversation": state.client_type == "visitor",
                "polling_fallback": True,
                "broker": settings.webchat_ws_broker,
            },
            "fallback_poll_ms": settings.webchat_ws_fallback_poll_ms,
            "heartbeat_ms": settings.webchat_ws_heartbeat_ms,
        }
    )


async def _handle_hello(websocket: WebSocket, db: Session, state: ConnectionState, message: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.webchat_ws_enabled:
        _record_auth_failed("unknown", "webchat_ws_disabled")
        await _send_error(websocket, "webchat_ws_disabled", "WebChat WebSocket runtime is disabled", retryable=False)
        await websocket.close(code=4403)
        return
    client_type = str(message.get("client_type") or "").strip().lower()
    if client_type not in {"agent", "visitor"}:
        _record_auth_failed("unknown", "invalid_client_type")
        await _send_error(websocket, "invalid_client_type", "client_type must be agent or visitor", retryable=False)
        await websocket.close(code=4400)
        return
    if client_type == "agent" and not settings.webchat_ws_admin_enabled:
        _record_auth_failed(client_type, "webchat_ws_admin_disabled")
        await _send_error(websocket, "webchat_ws_admin_disabled", "WebChat admin WebSocket runtime is disabled", retryable=False)
        await websocket.close(code=4403)
        return
    if client_type == "visitor" and not settings.webchat_ws_public_enabled:
        _record_auth_failed(client_type, "webchat_ws_public_disabled")
        record_webchat_websocket_fallback_polling("visitor", "public_ws_disabled")
        log_event(20, "websocket_fallback_polling", client_type="visitor", reason="public_ws_disabled")
        await _send_error(websocket, "webchat_ws_public_disabled", "WebChat public WebSocket runtime is disabled", retryable=False)
        await websocket.close(code=4403)
        return
    token = message.get("access_token") or _access_token_from_headers(websocket)
    current_user = _load_user(db, str(token)) if client_type == "agent" else None
    if client_type == "agent" and current_user is None:
        _record_auth_failed(client_type, "authentication_required")
        await _send_error(websocket, "authentication_required", "Authentication required", retryable=False)
        await websocket.close(code=4401)
        return
    snapshot = await webchat_realtime_hub.snapshot()
    if snapshot["connections"] >= settings.webchat_ws_max_connections:
        _record_auth_failed(client_type, "max_connections")
        await _send_error(websocket, "connection_limit_exceeded", "WebChat WebSocket connection limit exceeded", retryable=True)
        await websocket.close(code=4429)
        return
    if current_user is not None:
        existing_for_user = await webchat_realtime_hub.count_for_agent(current_user.id)
        if existing_for_user >= settings.webchat_ws_max_connections_per_user:
            _record_auth_failed(client_type, "max_connections_per_user")
            await _send_error(websocket, "connection_limit_exceeded", "WebChat WebSocket per-user connection limit exceeded", retryable=True)
            await websocket.close(code=4429)
            return
    state.client_type = client_type
    state.current_user = current_user
    state.visitor_token = str(message.get("visitor_token") or "") or websocket.headers.get("x-webchat-visitor-token")
    state.connection_id = await webchat_realtime_hub.connect(
        client_type=client_type,
        user_id=current_user.id if current_user else None,
    )
    record_webchat_websocket_connected(client_type)
    log_event(20, "websocket_connected", client_type=client_type)
    await _send_ready(websocket, state)

    conversation_id = message.get("conversation_id")
    ticket_id = message.get("ticket_id")
    if conversation_id or ticket_id:
        await _subscribe_conversation(websocket, db, state, message)


async def _subscribe_conversation(websocket: WebSocket, db: Session, state: ConnectionState, message: dict[str, Any]) -> None:
    if not state.client_type:
        await _send_error(websocket, "connection_not_ready", "Send connection.hello before subscribing", retryable=False)
        return
    last_event_id = int(message.get("last_event_id") or 0)
    if state.client_type == "visitor":
        public_id = str(message.get("conversation_id") or "").strip()
        try:
            conversation = validate_visitor_conversation(db, public_id=public_id, visitor_token=state.visitor_token)
        except HTTPException:
            _record_auth_failed("visitor", "invalid_visitor_conversation")
            raise
        if state.connection_id:
            existing_for_conversation = await webchat_realtime_hub.count_for_visitor_conversation(conversation.id)
            settings = get_settings()
            if existing_for_conversation >= settings.webchat_ws_max_connections_per_user:
                _record_auth_failed("visitor", "max_connections_per_conversation")
                await _send_error(websocket, "connection_limit_exceeded", "WebChat WebSocket per-conversation connection limit exceeded", retryable=True)
                await websocket.close(code=4429)
                return
            await webchat_realtime_hub.update_connection(state.connection_id, visitor_conversation_id=conversation.id)
        sub = ConversationSubscription(
            conversation_id=conversation.id,
            public_id=conversation.public_id,
            ticket_id=conversation.ticket_id,
            audience="visitor",
            last_event_id=last_event_id,
        )
    else:
        assert state.current_user is not None
        public_id = str(message.get("conversation_id") or "").strip() or None
        ticket_id = int(message["ticket_id"]) if message.get("ticket_id") is not None else None
        conversation, ticket = validate_agent_conversation(db, current_user=state.current_user, ticket_id=ticket_id, public_id=public_id)
        sub = ConversationSubscription(
            conversation_id=conversation.id,
            public_id=conversation.public_id,
            ticket_id=ticket.id,
            audience="admin",
            last_event_id=last_event_id,
        )
    assert state.conversations is not None
    state.conversations = [item for item in state.conversations if item.conversation_id != sub.conversation_id]
    state.conversations.append(sub)
    if state.connection_id:
        await webchat_realtime_hub.add_subscription(state.connection_id, f"conversation:{sub.conversation_id}")
    await websocket.send_json(
        {
            "type": "subscription.ready",
            "subscription": "conversation",
            "conversation_id": sub.public_id,
            "ticket_id": sub.ticket_id,
            "last_event_id": sub.last_event_id,
        }
    )
    await _send_conversation_replay(websocket, db, state, sub)


async def _subscribe_queue(websocket: WebSocket, db: Session, state: ConnectionState, message: dict[str, Any]) -> None:
    if state.client_type != "agent" or state.current_user is None:
        await _send_error(websocket, "permission_denied", "handoff queue subscriptions require an agent session", retryable=False)
        return
    view = str(message.get("view") or "requested").strip().lower()
    if view not in {"requested", "ai_active", "mine", "closed"}:
        await _send_error(websocket, "invalid_queue_view", "unsupported handoff queue view", retryable=False)
        return
    if view == "ai_active":
        ensure_can_monitor_webchat_ai(state.current_user, db)
    else:
        ensure_can_accept_webchat_handoff(state.current_user, db)
    last_event_id = int(message.get("last_event_id") or 0)
    sub = QueueSubscription(view=view, last_event_id=last_event_id)
    assert state.queues is not None
    state.queues = [item for item in state.queues if item.view != view]
    state.queues.append(sub)
    if state.connection_id:
        await webchat_realtime_hub.add_subscription(state.connection_id, f"handoff_queue:{view}")
    await websocket.send_json(
        {
            "type": "queue.snapshot",
            "view": view,
            "event_id": last_event_id,
            "data": list_handoff_queue(db, state.current_user, view=view, limit=50),
        }
    )


async def _send_conversation_replay(websocket: WebSocket, db: Session, state: ConnectionState, sub: ConversationSubscription) -> bool:
    db.expire_all()
    batch = list_conversation_event_envelopes(
        db,
        conversation_id=sub.conversation_id,
        after_id=sub.last_event_id,
        audience="visitor" if sub.audience == "visitor" else "admin",
        current_user=state.current_user,
    )
    events = batch.events
    for event in events:
        await websocket.send_json(event)
        record_webchat_websocket_event_sent(state.client_type, event.get("type"))
        log_event(20, "websocket_event_sent", client_type=state.client_type, event_type=event.get("type"))
        sub.last_event_id = max(sub.last_event_id, int(event["event_id"]))
    sub.last_event_id = max(sub.last_event_id, batch.scanned_last_event_id)
    if events:
        record_webchat_websocket_event_replay(state.client_type, "conversation", len(events))
        log_event(20, "websocket_event_replay", client_type=state.client_type, subscription="conversation", event_count=len(events))
    return bool(events)


async def _send_queue_updates(websocket: WebSocket, db: Session, state: ConnectionState, sub: QueueSubscription) -> bool:
    if state.current_user is None:
        return False
    db.expire_all()
    batch = list_admin_queue_event_envelopes(db, current_user=state.current_user, after_id=sub.last_event_id)
    events = batch.events
    if not events:
        sub.last_event_id = max(sub.last_event_id, batch.scanned_last_event_id)
        return False
    max_event_id = sub.last_event_id
    for event in events:
        await websocket.send_json(event)
        record_webchat_websocket_event_sent(state.client_type, event.get("type"))
        log_event(20, "websocket_event_sent", client_type=state.client_type, event_type=event.get("type"))
        max_event_id = max(max_event_id, int(event["event_id"]))
    record_webchat_websocket_event_replay(state.client_type, "handoff_queue", len(events))
    log_event(20, "websocket_event_replay", client_type=state.client_type, subscription="handoff_queue", event_count=len(events))
    sub.last_event_id = max(max_event_id, batch.scanned_last_event_id)
    await websocket.send_json(
        {
            "type": "queue.updated",
            "event_id": sub.last_event_id,
            "view": sub.view,
            "data": list_handoff_queue(db, state.current_user, view=sub.view, limit=50),
        }
    )
    record_webchat_websocket_event_sent(state.client_type, "queue.updated")
    return True


async def _send_replay(websocket: WebSocket, db: Session, state: ConnectionState) -> bool:
    delivered = False
    for sub in list(state.conversations or []):
        delivered = await _send_conversation_replay(websocket, db, state, sub) or delivered
    for sub in list(state.queues or []):
        delivered = await _send_queue_updates(websocket, db, state, sub) or delivered
    return delivered


async def _handle_command(websocket: WebSocket, db: Session, state: ConnectionState, message: dict[str, Any]) -> None:
    message_type = str(message.get("type") or "").strip()
    request_id = str(message.get("request_id") or "") or None
    try:
        if message_type == "ping":
            await websocket.send_json({"type": "pong", "request_id": request_id})
            return
        if message_type == "connection.hello":
            await _handle_hello(websocket, db, state, message)
            return
        if message_type == "subscribe.conversation":
            await _subscribe_conversation(websocket, db, state, message)
            return
        if message_type == "subscribe.handoff_queue":
            await _subscribe_queue(websocket, db, state, message)
            return
        if message_type == "handoff.force_takeover":
            if state.current_user is None:
                await _send_error(websocket, "permission_denied", "force takeover requires an agent session", request_id=request_id)
                return
            with managed_session(db):
                result = force_takeover_ticket(
                    db,
                    ticket_id=int(message["ticket_id"]),
                    current_user=state.current_user,
                    reason_code=message.get("reason_code") or "operator_forced_takeover",
                    note=message.get("note"),
                )
            await websocket.send_json({"type": "command.ok", "request_id": request_id, "command": message_type, "result": result})
            await _send_replay(websocket, db, state)
            return
        if message_type == "agent.reply":
            if state.current_user is None:
                await _send_error(websocket, "permission_denied", "agent reply requires an agent session", request_id=request_id)
                return
            ensure_can_send_outbound(state.current_user, db)
            with managed_session(db):
                result = admin_reply(
                    db,
                    int(message["ticket_id"]),
                    state.current_user,
                    body=str(message.get("body") or ""),
                    evidence_reference_id=message.get("evidence_reference_id"),
                )
            await websocket.send_json({"type": "command.ok", "request_id": request_id, "command": message_type, "result": result})
            await _send_replay(websocket, db, state)
            return
        await _send_error(websocket, "unknown_command", f"Unknown WebChat realtime command: {message_type}", request_id=request_id)
    except HTTPException as exc:
        await _send_error(websocket, "request_failed", str(exc.detail), retryable=exc.status_code >= 500, request_id=request_id)
    except (KeyError, TypeError, ValueError) as exc:
        await _send_error(websocket, "invalid_command", str(exc), retryable=False, request_id=request_id)


@router.websocket("/api/webchat/ws")
async def webchat_ws(websocket: WebSocket, db: Session = Depends(get_db)) -> None:
    await websocket.accept()
    settings = get_settings()
    state = ConnectionState()
    poll_seconds = max(0.1, settings.webchat_ws_replay_poll_ms / 1000)
    try:
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), timeout=max(1.0, settings.webchat_ws_hello_timeout_ms / 1000))
        except asyncio.TimeoutError:
            await _send_error(websocket, "hello_timeout", "connection.hello is required", retryable=False)
            await websocket.close(code=4408)
            return
        except (json.JSONDecodeError, ValueError, TypeError):
            await _send_error(websocket, "invalid_json", "WebSocket messages must be valid JSON objects", retryable=False)
            await websocket.close(code=4400)
            return
        await _handle_command(websocket, db, state, hello if isinstance(hello, dict) else {})
        if not state.connection_id:
            return
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=poll_seconds)
                if isinstance(message, dict):
                    await _handle_command(websocket, db, state, message)
                else:
                    await _send_error(websocket, "invalid_json", "WebSocket messages must be JSON objects", retryable=False)
            except asyncio.TimeoutError:
                pass
            except (json.JSONDecodeError, ValueError, TypeError):
                await _send_error(websocket, "invalid_json", "WebSocket messages must be valid JSON objects", retryable=False)
                await websocket.close(code=4400)
                return
            await _send_replay(websocket, db, state)
            state.hub_version = await webchat_realtime_hub.wait_for_event(
                last_seen_version=state.hub_version,
                timeout_seconds=min(poll_seconds, max(0.1, settings.webchat_ws_heartbeat_ms / 1000)),
            )
    except WebSocketDisconnect:
        pass
    finally:
        if state.connection_id:
            record_webchat_websocket_disconnected(state.client_type)
            log_event(20, "websocket_disconnected", client_type=state.client_type)
            await webchat_realtime_hub.disconnect(state.connection_id)
