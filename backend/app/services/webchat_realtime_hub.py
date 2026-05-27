from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from .observability import log_event, record_webchat_websocket_active_connections


@dataclass
class WebchatRealtimeConnection:
    connection_id: str
    client_type: str
    user_id: int | None = None
    visitor_conversation_id: int | None = None
    subscriptions: set[str] = field(default_factory=set)


class WebchatRealtimeHub:
    """In-process connection registry and wake-up path for durable DB replay.

    The database remains the source of truth for replay and multi-worker safety.
    This hub only avoids waiting for the next timed replay scan when the current
    process wrote a new WebchatEvent.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebchatRealtimeConnection] = {}
        self._lock = asyncio.Lock()
        self._version = 0
        self._event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is not loop:
            self._loop = loop
            self._event = asyncio.Event()

    async def connect(self, *, client_type: str, user_id: int | None = None, visitor_conversation_id: int | None = None) -> str:
        self.bind_loop()
        connection_id = uuid.uuid4().hex
        async with self._lock:
            self._connections[connection_id] = WebchatRealtimeConnection(
                connection_id=connection_id,
                client_type=client_type,
                user_id=user_id,
                visitor_conversation_id=visitor_conversation_id,
            )
            snapshot = self._snapshot_unlocked()
        record_webchat_websocket_active_connections(agents=snapshot["agents"], visitors=snapshot["visitors"])
        log_event(20, "websocket_active_connections", agents=snapshot["agents"], visitors=snapshot["visitors"], total=snapshot["connections"])
        return connection_id

    async def disconnect(self, connection_id: str) -> None:
        async with self._lock:
            self._connections.pop(connection_id, None)
            snapshot = self._snapshot_unlocked()
        record_webchat_websocket_active_connections(agents=snapshot["agents"], visitors=snapshot["visitors"])
        log_event(20, "websocket_active_connections", agents=snapshot["agents"], visitors=snapshot["visitors"], total=snapshot["connections"])

    async def update_connection(self, connection_id: str, **updates: Any) -> None:
        async with self._lock:
            record = self._connections.get(connection_id)
            if record is None:
                return
            for key, value in updates.items():
                if hasattr(record, key):
                    setattr(record, key, value)

    async def add_subscription(self, connection_id: str, subscription: str) -> None:
        async with self._lock:
            record = self._connections.get(connection_id)
            if record is not None:
                record.subscriptions.add(subscription)

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "connections": len(self._connections),
            "agents": sum(1 for item in self._connections.values() if item.client_type == "agent"),
            "visitors": sum(1 for item in self._connections.values() if item.client_type == "visitor"),
            "subscriptions": sum(len(item.subscriptions) for item in self._connections.values()),
        }

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return self._snapshot_unlocked()

    async def count_for_agent(self, user_id: int) -> int:
        async with self._lock:
            return sum(1 for item in self._connections.values() if item.client_type == "agent" and item.user_id == user_id)

    async def count_for_visitor_conversation(self, conversation_id: int) -> int:
        async with self._lock:
            return sum(
                1
                for item in self._connections.values()
                if item.client_type == "visitor" and item.visitor_conversation_id == conversation_id
            )

    def notify_event_sync(self, *, conversation_id: int | None = None, ticket_id: int | None = None, event_type: str | None = None) -> None:
        self._version += 1
        loop = self._loop
        event = self._event
        if loop is None or event is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(event.set)

    async def wait_for_event(self, *, last_seen_version: int, timeout_seconds: float) -> int:
        self.bind_loop()
        event = self._event
        if self._version != last_seen_version:
            return self._version
        if event is None:
            await asyncio.sleep(timeout_seconds)
            return self._version
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.05, timeout_seconds))
        except asyncio.TimeoutError:
            pass
        finally:
            if event.is_set():
                event.clear()
        return self._version


webchat_realtime_hub = WebchatRealtimeHub()


def notify_webchat_realtime_event_sync(*, conversation_id: int | None = None, ticket_id: int | None = None, event_type: str | None = None) -> None:
    webchat_realtime_hub.notify_event_sync(
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        event_type=event_type,
    )
