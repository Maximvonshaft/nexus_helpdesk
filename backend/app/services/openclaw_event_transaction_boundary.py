from __future__ import annotations

from typing import Any

_PATCHED = False


def _event_cursor(event: dict[str, Any], fallback: Any = None) -> Any:
    return event.get("cursor") or event.get("nextCursor") or event.get("offset") or fallback


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or event.get("eventType") or "message")


def _record_unhandled_event_failure(openclaw_bridge: Any, db: Any, *, event: dict[str, Any], source: str, exc: Exception) -> None:
    route = openclaw_bridge._extract_event_route(event)
    session_key = openclaw_bridge._extract_event_session_key(event)
    recipient = route.get("recipient")
    row = openclaw_bridge.persist_unresolved_openclaw_event(
        db,
        source=source,
        session_key=session_key,
        event_type=_event_type(event),
        recipient=recipient,
        source_chat_id=recipient,
        preferred_reply_contact=recipient,
        payload=event,
    )
    row.status = "failed"
    row.last_error = f"Unhandled OpenClaw event exception: {type(exc).__name__}"
    row.updated_at = openclaw_bridge.utc_now()


def _commit_cursor(openclaw_bridge: Any, db: Any, *, source: str, cursor_value: Any) -> None:
    if cursor_value is not None:
        openclaw_bridge.upsert_openclaw_sync_cursor(db, source=source, cursor_value=str(cursor_value))
    db.flush()
    db.commit()


def _process_events_with_event_boundary(
    openclaw_bridge: Any,
    db: Any,
    *,
    events: list[Any],
    source: str,
    client: Any | None,
    batch_next_cursor: Any,
) -> int:
    processed = 0
    last_cursor = None
    for event in events:
        if not isinstance(event, dict):
            continue
        current_cursor = _event_cursor(event, last_cursor)
        try:
            if openclaw_bridge.process_openclaw_inbound_event(db, event=event, source=source, client=client):
                processed += 1
            _commit_cursor(openclaw_bridge, db, source=source, cursor_value=current_cursor)
        except Exception as exc:
            db.rollback()
            try:
                _record_unhandled_event_failure(openclaw_bridge, db, event=event, source=source, exc=exc)
                _commit_cursor(openclaw_bridge, db, source=source, cursor_value=current_cursor)
                openclaw_bridge.LOGGER.warning(
                    "openclaw_event_attempt_exception_recovered",
                    extra={
                        "event_payload": {
                            "source": source,
                            "cursor": current_cursor,
                            "event_type": _event_type(event),
                            "error_type": type(exc).__name__,
                        }
                    },
                )
            except Exception as recovery_exc:
                db.rollback()
                openclaw_bridge.LOGGER.exception(
                    "openclaw_event_attempt_exception_recovery_failed",
                    extra={
                        "event_payload": {
                            "source": source,
                            "cursor": current_cursor,
                            "event_type": _event_type(event),
                            "error_type": type(exc).__name__,
                            "recovery_error_type": type(recovery_exc).__name__,
                        }
                    },
                )
                raise
        last_cursor = current_cursor
    if batch_next_cursor is not None and batch_next_cursor != last_cursor:
        _commit_cursor(openclaw_bridge, db, source=source, cursor_value=batch_next_cursor)
    return processed


def _consume_openclaw_events_once_with_event_boundary(db: Any, *, source: str = "default", timeout_seconds: int | None = None) -> int:
    from . import openclaw_bridge
    from . import openclaw_p0_runtime_security as p0

    settings = openclaw_bridge.settings
    timeout_seconds = timeout_seconds or settings.openclaw_sync_poll_timeout_seconds
    cursor_row = db.query(openclaw_bridge.OpenClawSyncCursor).filter(openclaw_bridge.OpenClawSyncCursor.source == source).first()
    cursor_str = cursor_row.cursor_value if cursor_row else None

    try:
        after_cursor = int(cursor_str) if cursor_str is not None else 0
    except ValueError:
        after_cursor = 0

    bridge_success = False
    payload: dict[str, Any] | None = None

    if settings.openclaw_bridge_enabled:
        wait_res = openclaw_bridge.wait_openclaw_bridge_events(after_cursor=after_cursor, timeout_seconds=timeout_seconds)
        if wait_res is not None:
            bridge_success = True
            if wait_res.get("event"):
                poll_res = openclaw_bridge.poll_openclaw_bridge_events(after_cursor=after_cursor)
                if poll_res is not None:
                    payload = poll_res
                    openclaw_bridge.LOGGER.info(
                        "openclaw_bridge_event_read_success",
                        extra={"event_payload": {"action": "events_poll", "events_count": len(payload.get("events", []))}},
                    )
                else:
                    payload = {"events": [wait_res["event"]], "nextCursor": wait_res["event"].get("cursor", after_cursor)}
                    openclaw_bridge.LOGGER.info(
                        "openclaw_bridge_event_read_success",
                        extra={"event_payload": {"action": "events_wait", "events_count": 1}},
                    )
            else:
                payload = {"events": [], "nextCursor": after_cursor}
        else:
            openclaw_bridge.LOGGER.warning(
                "openclaw_bridge_event_fallback",
                extra={"event_payload": {"reason": "bridge_failed_or_missing_data"}},
            )

    if not bridge_success:
        if not openclaw_bridge._local_mcp_fallback_allowed():
            openclaw_bridge.LOGGER.warning(
                "openclaw_event_local_mcp_fallback_skipped_in_remote_gateway_mode",
                extra={"event_payload": {"after_cursor": after_cursor, "reason": "remote_gateway_local_mcp_disabled"}},
            )
            return 0

        openclaw_bridge.LOGGER.info("openclaw_mcp_event_invoked", extra={"event_payload": {"after_cursor": after_cursor}})
        with openclaw_bridge.OpenClawMCPClient() as client:
            try:
                payload = client.events_wait(cursor=after_cursor, timeout_seconds=timeout_seconds)
                if not isinstance(payload, dict):
                    payload = client.events_poll(cursor=after_cursor)
            except openclaw_bridge.OpenClawMCPError as exc:
                openclaw_bridge.LOGGER.warning("openclaw_mcp_event_failed", extra={"event_payload": {"error": str(exc)}})
                payload = client.events_poll(cursor=after_cursor)

            if not isinstance(payload, dict):
                return 0

            events, next_cursor = p0._extract_events_and_cursor(payload, after_cursor)
            return _process_events_with_event_boundary(
                openclaw_bridge,
                db,
                events=events,
                source=source,
                client=client,
                batch_next_cursor=next_cursor,
            )

    if not isinstance(payload, dict):
        return 0

    events, next_cursor = p0._extract_events_and_cursor(payload, after_cursor)
    return _process_events_with_event_boundary(
        openclaw_bridge,
        db,
        events=events,
        source=source,
        client=None,
        batch_next_cursor=next_cursor,
    )


def apply_openclaw_event_transaction_boundary_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from . import openclaw_bridge

    openclaw_bridge.consume_openclaw_events_once = _consume_openclaw_events_once_with_event_boundary
    _PATCHED = True
