from __future__ import annotations

import functools
import hashlib
import subprocess
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from ..enums import MessageStatus
from ..utils.time import utc_now

_PATCHED = False
_REDACTED_COMMAND = "[redacted_command]"
_REDACTED_MESSAGE = "[redacted_message_body]"
_FINGERPRINT_PREFIX = "sha256:"


def _stable_fingerprint(value: Any) -> str | None:
    if value in (None, ""):
        return None
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    return f"{_FINGERPRINT_PREFIX}{digest}"


def mask_target(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) <= 4:
        return "***"
    if len(text) <= 8:
        return f"***{text[-2:]}"
    return f"{text[:2]}***{text[-4:]}"


def _normalized_key(key: Any) -> str:
    return str(key).replace("-", "_").lower()


def redact_route_context(route: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(route, dict):
        return {}
    redacted = dict(route)
    if "target" in redacted:
        redacted["target"] = mask_target(redacted.get("target"))
    if "recipient" in redacted:
        redacted["recipient"] = mask_target(redacted.get("recipient"))
    if "preferred_reply_contact" in redacted:
        redacted["preferred_reply_contact"] = mask_target(redacted.get("preferred_reply_contact"))
    if "source_chat_id" in redacted:
        redacted["source_chat_id"] = mask_target(redacted.get("source_chat_id"))
    if "session_key" in redacted:
        redacted["session_key"] = _stable_fingerprint(redacted.get("session_key"))
    if "sessionKey" in redacted:
        redacted["sessionKey"] = _stable_fingerprint(redacted.get("sessionKey"))
    if "idempotency_key" in redacted:
        redacted["idempotency_key"] = _stable_fingerprint(redacted.get("idempotency_key"))
    if "idempotencyKey" in redacted:
        redacted["idempotencyKey"] = _stable_fingerprint(redacted.get("idempotencyKey"))
    return redacted


def sanitize_event_payload(value: Any, *, key: str | None = None) -> Any:
    normalized = _normalized_key(key or "")
    if normalized in {"command"}:
        return _REDACTED_COMMAND
    if normalized in {"body", "message", "message_body", "messagebody", "text", "content", "contenttext", "content_text"}:
        return _REDACTED_MESSAGE
    if normalized in {"target", "recipient", "preferred_reply_contact", "source_chat_id"}:
        return mask_target(value)
    if normalized in {"session_key", "sessionkey", "idempotency_key", "idempotencykey"}:
        return _stable_fingerprint(value)

    if isinstance(value, dict):
        if normalized == "route":
            return redact_route_context(value)
        sanitized: dict[str, Any] = {}
        for child_key, child_value in value.items():
            sanitized[str(child_key)] = sanitize_event_payload(child_value, key=str(child_key))
        return sanitized
    if isinstance(value, list):
        return [sanitize_event_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_event_payload(item) for item in value)
    return value


def _sanitize_extra(extra: Any) -> Any:
    if not isinstance(extra, dict):
        return extra
    sanitized = dict(extra)
    if "event_payload" in sanitized:
        sanitized["event_payload"] = sanitize_event_payload(sanitized["event_payload"])
    return sanitized


class _SanitizingLogger:
    _openclaw_p0_sanitizing_proxy = True

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)

    def _call(self, level: str, message: str, *args: Any, **kwargs: Any) -> Any:
        if "extra" in kwargs:
            kwargs = {**kwargs, "extra": _sanitize_extra(kwargs.get("extra"))}
        return getattr(self._logger, level)(message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> Any:
        return self._call("debug", message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> Any:
        return self._call("info", message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> Any:
        return self._call("warning", message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> Any:
        return self._call("error", message, *args, **kwargs)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> Any:
        return self._call("exception", message, *args, **kwargs)


def _wrap_logger(logger: Any) -> Any:
    if getattr(logger, "_openclaw_p0_sanitizing_proxy", False):
        return logger
    return _SanitizingLogger(logger)


def _sanitize_log_event(original):
    if getattr(original, "_openclaw_p0_sanitized_log_event", False):
        return original

    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if "payload" in kwargs:
            kwargs = {**kwargs, "payload": sanitize_event_payload(kwargs.get("payload"))}
        return original(*args, **kwargs)

    wrapper._openclaw_p0_sanitized_log_event = True  # type: ignore[attr-defined]
    return wrapper


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _response_content_type(resp: Any) -> str | None:
    headers = getattr(resp, "headers", None)
    getter = getattr(headers, "get_content_type", None)
    if callable(getter):
        return getter()
    if hasattr(headers, "get"):
        raw = headers.get("Content-Type") or headers.get("content-type")
        if isinstance(raw, str) and raw:
            return raw.split(";", 1)[0].strip()
    return None


def _validate_attachment_url(openclaw_bridge: Any, url: str):
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    hostname = parsed.hostname.lower()
    if not openclaw_bridge._host_matches_allowlist(hostname):
        return None
    if not openclaw_bridge._resolved_host_is_public(hostname):
        return None
    return parsed


def _safe_try_fetch_remote_attachment(url: str, metadata: dict[str, Any]) -> tuple[bytes | None, str | None, str | None]:
    from . import openclaw_bridge

    settings = openclaw_bridge.settings
    if not settings.openclaw_attachment_url_fetch_enabled:
        return None, None, None
    if _validate_attachment_url(openclaw_bridge, url) is None:
        return None, None, None

    request = urllib.request.Request(url, headers={"User-Agent": "helpdesk-suite/attachment-fetch"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=settings.openclaw_attachment_fetch_timeout_seconds) as resp:
            final_url = getattr(resp, "geturl", lambda: url)()
            if _validate_attachment_url(openclaw_bridge, final_url) is None:
                return None, None, None
            media_type = metadata.get("contentType") or metadata.get("mimeType") or _response_content_type(resp)
            if media_type not in settings.openclaw_attachment_allowed_mime_types:
                return None, None, None
            content = openclaw_bridge._read_bounded_response(resp)
            if content is None:
                return None, None, None
            return content, media_type, metadata.get("filename") or metadata.get("name")
    except urllib.error.HTTPError as exc:
        if 300 <= int(getattr(exc, "code", 0) or 0) < 400:
            return None, None, None
        return None, None, None
    except Exception:
        return None, None, None


def _safe_dispatch_via_openclaw_cli(
    *,
    channel: str,
    target: str,
    body: str,
    account_id: str | None = None,
    thread_id: str | None = None,
) -> tuple[MessageStatus, str | None, object | None]:
    from . import openclaw_bridge

    settings = openclaw_bridge.settings
    openclaw_bin = settings.openclaw_bin
    if not openclaw_bin:
        return MessageStatus.failed, "OPENCLAW_BIN is not configured", None

    cmd = [openclaw_bin, "message", "send", "--channel", channel, "--target", target, "--message", body]
    if account_id:
        cmd.extend(["--account", account_id])
    if thread_id:
        cmd.extend(["--thread-id", thread_id])

    openclaw_bridge.LOGGER.warning(
        "openclaw_cli_fallback_invoked",
        extra={
            "event_payload": sanitize_event_payload(
                {
                    "dispatch": "cli_fallback",
                    "channel": channel,
                    "target": target,
                    "account_id": account_id,
                    "thread_id": thread_id,
                    "message_body": _REDACTED_MESSAGE,
                }
            )
        },
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        openclaw_bridge.LOGGER.info(
            "openclaw_cli_fallback_success",
            extra={"event_payload": sanitize_event_payload({"dispatch": "cli_fallback", "channel": channel, "target": target})},
        )
        return MessageStatus.sent, "sent_via_openclaw_cli_fallback", utc_now()
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()[:500] if exc.stderr else "openclaw send failed"
        openclaw_bridge.LOGGER.warning(
            "openclaw_cli_fallback_failed",
            extra={
                "event_payload": sanitize_event_payload(
                    {"dispatch": "cli_fallback", "channel": channel, "target": target, "error": stderr}
                )
            },
        )
        return MessageStatus.failed, stderr, None
    except Exception as exc:
        openclaw_bridge.LOGGER.warning(
            "openclaw_cli_fallback_failed",
            extra={
                "event_payload": sanitize_event_payload(
                    {"dispatch": "cli_fallback", "channel": channel, "target": target, "error": str(exc)}
                )
            },
        )
        return MessageStatus.failed, str(exc), None


def _extract_events_and_cursor(payload: dict[str, Any], after_cursor: int) -> tuple[list[Any], Any]:
    next_cursor = payload.get("cursor") or payload.get("nextCursor")
    events = payload.get("events") or payload.get("items") or []
    if not events and payload.get("event"):
        event = payload.get("event")
        events = [event]
        if next_cursor is None and isinstance(event, dict):
            next_cursor = event.get("cursor", after_cursor)
    return events, next_cursor


def _process_events(openclaw_bridge: Any, db: Any, *, events: list[Any], source: str, client: Any | None) -> int:
    processed = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if openclaw_bridge.process_openclaw_inbound_event(db, event=event, source=source, client=client):
            processed += 1
    return processed


def _safe_consume_openclaw_events_once(db: Any, *, source: str = "default", timeout_seconds: int | None = None) -> int:
    from . import openclaw_bridge

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

            events, next_cursor = _extract_events_and_cursor(payload, after_cursor)
            processed = _process_events(openclaw_bridge, db, events=events, source=source, client=client)
            if next_cursor is not None:
                openclaw_bridge.upsert_openclaw_sync_cursor(db, source=source, cursor_value=str(next_cursor))
                db.flush()
            return processed

    if not isinstance(payload, dict):
        return 0

    events, next_cursor = _extract_events_and_cursor(payload, after_cursor)
    processed = _process_events(openclaw_bridge, db, events=events, source=source, client=None)
    if next_cursor is not None:
        openclaw_bridge.upsert_openclaw_sync_cursor(db, source=source, cursor_value=str(next_cursor))
        db.flush()
    return processed


def apply_openclaw_p0_runtime_security_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from . import message_dispatch, openclaw_bridge

    openclaw_bridge.LOGGER = _wrap_logger(openclaw_bridge.LOGGER)
    message_dispatch.LOGGER = _wrap_logger(message_dispatch.LOGGER)

    openclaw_bridge.log_event = _sanitize_log_event(openclaw_bridge.log_event)
    message_dispatch.log_event = _sanitize_log_event(message_dispatch.log_event)

    openclaw_bridge._try_fetch_remote_attachment = _safe_try_fetch_remote_attachment
    openclaw_bridge.dispatch_via_openclaw_cli = _safe_dispatch_via_openclaw_cli
    openclaw_bridge.consume_openclaw_events_once = _safe_consume_openclaw_events_once

    # message_dispatch imports provider functions directly, so update its live
    # references as well when this package-level patch is applied after import.
    message_dispatch.dispatch_via_openclaw_cli = _safe_dispatch_via_openclaw_cli

    _PATCHED = True
