from __future__ import annotations

from dataclasses import dataclass

from .webchat_realtime_hub import notify_webchat_realtime_event_sync, webchat_realtime_hub


@dataclass(frozen=True)
class RealtimeBrokerStatus:
    name: str
    durable_replay: bool
    cross_worker_safe: bool


def webchat_realtime_broker_status(name: str) -> RealtimeBrokerStatus:
    normalized = (name or "database").strip().lower()
    if normalized == "memory":
        return RealtimeBrokerStatus(name="memory", durable_replay=True, cross_worker_safe=False)
    return RealtimeBrokerStatus(name="database", durable_replay=True, cross_worker_safe=True)


def publish_webchat_event_sync(*, conversation_id: int | None, ticket_id: int | None, event_type: str | None) -> None:
    notify_webchat_realtime_event_sync(
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        event_type=event_type,
    )


__all__ = ["RealtimeBrokerStatus", "publish_webchat_event_sync", "webchat_realtime_broker_status", "webchat_realtime_hub"]
