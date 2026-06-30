from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows-only test fallback.
    class _NoopFcntl:
        LOCK_EX = 0
        LOCK_SH = 0
        LOCK_UN = 0

        @staticmethod
        def flock(*_args, **_kwargs) -> None:
            return None

    fcntl = _NoopFcntl()

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import MessageStatus
from ..services.permissions import CAP_OUTBOUND_SEND, CAP_TICKET_READ, ensure_capability
from .deps import get_current_user

router = APIRouter(prefix="/api/whatsapp-lite", tags=["whatsapp-lite"])

EXCLUDED_TEST_RECIPIENTS = {
    "+41798559737",
}

OUTBOX_MIRROR_FILENAME = "whatsapp-lite-outbox.jsonl"
LEGACY_OUTBOX_MIRROR_FILENAME = "external_channel-whatsapp-lite-outbox.jsonl"
OUTBOX_MIRROR_DIRNAME = ".whatsapp-lite-state"
OUTBOX_MIRROR_MAX_BYTES = 5 * 1024 * 1024


def list_external_channel_conversations(*, limit: int = 100, channel: str | None = None) -> dict[str, Any]:
    """Compatibility hook for historical tests; the live legacy source is retired."""
    return {"conversations": [], "source": "legacy_whatsapp_lite_retired", "limit": limit, "channel": channel}


def read_external_channel_bridge_conversation(session_key: str, limit: int = 50) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    return None, None


def fetch_external_channel_bridge_attachments(session_key: str, message_id: str) -> list[dict[str, Any]]:
    return []


def dispatch_via_external_channel_bridge(**kwargs) -> tuple[MessageStatus, str | None, object | None]:
    return MessageStatus.failed, "legacy_whatsapp_lite_source_retired", None


class WhatsAppLiteConversation(BaseModel):
    session_key: str
    channel: str = "whatsapp"
    recipient: str | None = None
    account_id: str | None = None
    thread_id: str | None = None
    source_agent: str | None = None
    display_name: str
    updated_at: str | None = None
    latest_message: str | None = None
    source: str = "legacy_whatsapp_lite"


class WhatsAppLiteAttachment(BaseModel):
    type: str = "file"
    mime_type: str | None = None
    caption: str | None = None
    thumbnail_url: str | None = None
    download_url: str | None = None
    width: int | str | None = None
    height: int | str | None = None
    storage_status: str | None = None
    filename: str | None = None


class WhatsAppLiteMessage(BaseModel):
    id: str
    author: str
    body: str
    timestamp: str | None = None
    attachments: list[WhatsAppLiteAttachment] = Field(default_factory=list)


class WhatsAppLiteConversationPage(BaseModel):
    items: list[WhatsAppLiteConversation]
    source: str = "legacy_whatsapp_lite"
    next_cursor: str | None = None
    total_visible: int | None = None


class WhatsAppLiteConversationDetail(BaseModel):
    conversation: WhatsAppLiteConversation
    messages: list[WhatsAppLiteMessage]
    source: str = "legacy_whatsapp_lite"


class WhatsAppLiteSendRequest(BaseModel):
    session_key: str = Field(min_length=1)
    body: str = Field(min_length=1)
    recipient: str | None = None
    account_id: str | None = None
    thread_id: str | None = None


class WhatsAppLiteSendResponse(BaseModel):
    ok: bool
    status: str
    provider_status: str | None = None
    sent_at: str | None = None


def _items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "conversations", "sessions", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _route(row: dict[str, Any]) -> dict[str, Any]:
    route = row.get("route")
    return route if isinstance(route, dict) else {}


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except Exception:
            return None
    return None


def _outbox_mirror_path() -> Path:
    configured = os.getenv("WHATSAPP_LITE_OUTBOX_MIRROR_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "uploads" / OUTBOX_MIRROR_DIRNAME / OUTBOX_MIRROR_FILENAME


def _legacy_outbox_mirror_path() -> Path:
    return Path(__file__).resolve().parents[2] / "uploads" / LEGACY_OUTBOX_MIRROR_FILENAME


def _outbox_mirror_max_bytes() -> int:
    raw = os.getenv("WHATSAPP_LITE_OUTBOX_MIRROR_MAX_BYTES")
    if not raw:
        return OUTBOX_MIRROR_MAX_BYTES
    try:
        return max(1024 * 1024, int(raw))
    except ValueError:
        return OUTBOX_MIRROR_MAX_BYTES


def _rotate_outbox_mirror_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < _outbox_mirror_max_bytes():
            return
        rotated = path.with_suffix(path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        os.replace(path, rotated)
        try:
            os.chmod(rotated, 0o600)
        except OSError:
            pass
    except OSError:
        return


def _append_outbox_mirror(*, session_key: str, body: str, timestamp: str | None, operator: str | None) -> None:
    text = body.strip()
    if not session_key or not text:
        return
    record = {
        "id": f"whatsapp-lite-human-{uuid4().hex}",
        "session_key": session_key,
        "author": "human",
        "body": text,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "operator": operator,
        "source": "whatsapp_lite_console",
    }
    path = _outbox_mirror_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            _rotate_outbox_mirror_if_needed(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        # Sending succeeded already; transcript mirroring must not turn delivery into a failure.
        return


def _read_outbox_mirror(session_key: str, *, limit: int = 200) -> list[WhatsAppLiteMessage]:
    path = _outbox_mirror_path()
    legacy_path = _legacy_outbox_mirror_path()
    paths = [path] if path == legacy_path or os.getenv("WHATSAPP_LITE_OUTBOX_MIRROR_PATH") else [path, legacy_path]
    if not session_key or not any(candidate.exists() for candidate in paths):
        return []
    messages: list[WhatsAppLiteMessage] = []
    try:
        for candidate in paths:
            if not candidate.exists():
                continue
            lock_path = candidate.with_suffix(candidate.suffix + ".lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
                with candidate.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(record, dict) or record.get("session_key") != session_key:
                            continue
                        body = _str(record.get("body"))
                        if not body:
                            continue
                        messages.append(
                            WhatsAppLiteMessage(
                                id=_str(record.get("id")) or f"whatsapp-lite-human-{len(messages)}",
                                author="human",
                                body=body,
                                timestamp=_timestamp(record.get("timestamp")),
                            )
                        )
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        return []
    return sorted(messages[-limit:], key=lambda item: item.timestamp or "")


def _merge_visible_messages(*message_groups: list[WhatsAppLiteMessage]) -> list[WhatsAppLiteMessage]:
    seen: set[tuple[Any, ...]] = set()
    messages: list[WhatsAppLiteMessage] = []
    for message in [message for group in message_groups for message in group]:
        attachment_key = tuple(
            (
                attachment.type,
                attachment.mime_type,
                attachment.caption,
                attachment.thumbnail_url,
                attachment.download_url,
                attachment.filename,
            )
            for attachment in message.attachments
        )
        key = (message.author, message.timestamp or "", message.body, attachment_key)
        if key in seen:
            continue
        seen.add(key)
        messages.append(message)
    return sorted(messages, key=lambda item: item.timestamp or "")


def _trim_visible_messages(messages: list[WhatsAppLiteMessage], *, limit: int) -> list[WhatsAppLiteMessage]:
    if limit <= 0:
        return []
    return messages[-limit:]


def _session_key(row: dict[str, Any]) -> str:
    return _str(row.get("sessionKey") or row.get("session_key") or row.get("key") or row.get("id")) or ""


def _canonical_whatsapp_direct_session_key(session_key: str) -> str:
    marker = ":whatsapp:direct:"
    if marker not in session_key:
        return session_key
    prefix, peer_part = session_key.split(marker, 1)
    peer = peer_part.split(":turn:", 1)[0]
    return f"{prefix}{marker}{peer}"


def _is_turn_session(session_key: str) -> bool:
    marker = ":whatsapp:direct:"
    if marker not in session_key:
        return False
    return ":turn:" in session_key.split(marker, 1)[1]


def _timestamp_sort_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return 0.0
    return 0.0


def _row_updated_sort_value(row: dict[str, Any]) -> float:
    return _timestamp_sort_value(row.get("updatedAt") or row.get("updated_at") or row.get("lastUpdatedAt"))


def _source_agent(session_key: str) -> str | None:
    parts = session_key.split(":")
    if len(parts) >= 2 and parts[0] == "agent":
        return parts[1] or None
    return None


def _recipient_from_row(row: dict[str, Any]) -> str | None:
    route = _route(row)
    recipient = _str(row.get("recipient") or route.get("recipient") or row.get("lastTo"))
    session_key = _session_key(row)
    if not recipient and ":direct:" in session_key:
        recipient = _str(session_key.rsplit(":direct:", 1)[-1].split(":turn:", 1)[0])
    if recipient and ":turn:" in recipient:
        recipient = _str(recipient.split(":turn:", 1)[0])
    return recipient


def _is_whatsapp_direct(row: dict[str, Any]) -> bool:
    route = _route(row)
    session_key = _session_key(row)
    channel = _str(row.get("channel") or route.get("channel") or row.get("lastChannel"))
    recipient = _recipient_from_row(row)
    if not session_key.startswith("agent:") or ":whatsapp:direct:" not in session_key:
        return False
    if channel and channel != "whatsapp":
        return False
    if recipient in EXCLUDED_TEST_RECIPIENTS:
        return False
    if recipient and ("@g.us" in recipient or "status@broadcast" in recipient):
        return False
    return True


def _latest_whatsapp_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=_row_updated_sort_value)


def _best_whatsapp_conversation_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest = _latest_whatsapp_row(rows)
    base_rows = [row for row in rows if not _is_turn_session(_session_key(row))]
    selected = _latest_whatsapp_row(base_rows) if base_rows else latest
    representative = dict(selected)
    if latest is not selected:
        for key in (
            "updatedAt",
            "updated_at",
            "lastUpdatedAt",
            "lastMessage",
            "message",
            "displayName",
            "display_name",
        ):
            if latest.get(key) not in (None, ""):
                representative[key] = latest[key]
    return representative


def _dedupe_whatsapp_direct_rows(rows: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or not _is_whatsapp_direct(row):
            continue
        canonical_key = _canonical_whatsapp_direct_session_key(_session_key(row))
        if not canonical_key:
            continue
        grouped.setdefault(canonical_key, []).append(row)
    representatives = [_best_whatsapp_conversation_row(group) for group in grouped.values()]
    return sorted(representatives, key=_row_updated_sort_value, reverse=True)


def _related_whatsapp_direct_rows(session_key: str, *, limit: int = 200) -> list[dict[str, Any]]:
    canonical_key = _canonical_whatsapp_direct_session_key(session_key)
    if not canonical_key:
        return []
    try:
        payload = list_external_channel_conversations(limit=limit, channel="whatsapp")
    except Exception:
        return []
    rows = [
        row
        for row in _items(payload)
        if isinstance(row, dict)
        and _is_whatsapp_direct(row)
        and _canonical_whatsapp_direct_session_key(_session_key(row)) == canonical_key
    ]
    return sorted(rows, key=_row_updated_sort_value, reverse=True)


def _unique_session_keys(rows: list[dict[str, Any]], *extra_keys: str) -> list[str]:
    keys: list[str] = []
    for key in extra_keys:
        clean = _str(key)
        if clean and clean not in keys:
            keys.append(clean)
    for row in rows:
        key = _session_key(row)
        if key and key not in keys:
            keys.append(key)
    return keys


def _conversation_from_row(row: dict[str, Any]) -> WhatsAppLiteConversation | None:
    route = _route(row)
    session_key = _session_key(row)
    if not session_key:
        return None
    recipient = _recipient_from_row(row)
    account_id = _str(row.get("accountId") or row.get("account_id") or route.get("accountId") or route.get("account_id") or row.get("lastAccountId"))
    thread_id = _str(row.get("threadId") or row.get("thread_id") or route.get("threadId") or route.get("thread_id") or row.get("lastThreadId"))
    display_name = (
        _str(row.get("displayName"))
        or _str(row.get("display_name"))
        or recipient
        or session_key
    )
    return WhatsAppLiteConversation(
        session_key=session_key,
        recipient=recipient,
        account_id=account_id,
        thread_id=thread_id,
        source_agent=_source_agent(session_key),
        display_name=display_name,
        updated_at=_timestamp(row.get("updatedAt") or row.get("updated_at") or row.get("lastUpdatedAt")),
        latest_message=_visible_text(row.get("lastMessage") or row.get("message")) or None,
    )


def _matches_conversation_query(conversation: WhatsAppLiteConversation, q: str | None) -> bool:
    query = (q or "").strip().lower()
    if not query:
        return True
    searchable = " ".join(
        value or ""
        for value in (
            conversation.display_name,
            conversation.recipient,
            conversation.latest_message,
            conversation.session_key,
            conversation.source_agent,
        )
    ).lower()
    return query in searchable


def _offset_from_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_conversation_cursor")


def _visible_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, dict):
        return ""
    content = message.get("content") or message.get("text") or message.get("message") or message.get("body")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return ""


def _message_id(message: dict[str, Any], index: int) -> str:
    external_channel = message.get("__external_channel")
    if isinstance(external_channel, dict):
        for key in ("id", "seq"):
            value = _str(external_channel.get(key))
            if value:
                return value
    for key in ("id", "messageId", "idempotencyKey", "timestamp"):
        value = _str(message.get(key))
        if value:
            return value
    return f"external_channel-{index}"


def _is_media_placeholder(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "[user sent media without caption]",
        "[customer sent media without caption]",
        "user sent media without caption",
        "customer sent media without caption",
    }


def _raw_media_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    media_paths = message.get("MediaPaths") or message.get("mediaPaths") or message.get("MediaPath") or message.get("mediaPath")
    if not media_paths:
        return []
    paths = media_paths if isinstance(media_paths, list) else [media_paths]
    media_types = message.get("MediaTypes") or message.get("mediaTypes") or message.get("MediaType") or message.get("mediaType")
    types = media_types if isinstance(media_types, list) else ([media_types] if media_types else [])
    attachments = []
    for index, path in enumerate(paths):
        mime_type = types[index] if index < len(types) else (types[0] if types else "image/jpeg")
        attachments.append(
            {
                "type": "image" if str(mime_type or "").startswith("image/") else "file",
                "mime_type": mime_type,
                "caption": None,
                "thumbnail_url": None,
                "download_url": None,
                "storage_status": "external_channel_media_referenced",
                "filename": str(path).rsplit("/", 1)[-1] if path else None,
            }
        )
    return attachments


def _attachment_value(attachment: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attachment.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_attachments(raw_attachments: list[Any], fallback_caption: str | None = None) -> list[WhatsAppLiteAttachment]:
    attachments: list[WhatsAppLiteAttachment] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        mime_type = _attachment_value(raw, "mime_type", "mimeType", "contentType", "type")
        thumbnail_url = _attachment_value(raw, "thumbnail_url", "thumbnailUrl", "dataUrl", "data_url")
        download_url = _attachment_value(raw, "download_url", "downloadUrl", "url")
        attachment_type = _attachment_value(raw, "type", "kind") or ("image" if str(mime_type or "").startswith("image/") else "file")
        if attachment_type == mime_type and str(mime_type or "").startswith("image/"):
            attachment_type = "image"
        attachments.append(
            WhatsAppLiteAttachment(
                type="image" if attachment_type == "image" or str(mime_type or "").startswith("image/") else "file",
                mime_type=str(mime_type) if mime_type else None,
                caption=_str(_attachment_value(raw, "caption", "text")) or fallback_caption,
                thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
                download_url=str(download_url) if download_url else None,
                width=_attachment_value(raw, "width"),
                height=_attachment_value(raw, "height"),
                storage_status=_str(_attachment_value(raw, "storage_status", "storageStatus")),
                filename=_str(_attachment_value(raw, "filename", "name")),
            )
        )
    return attachments


def _message_attachments(session_key: str, message_id: str, raw: dict[str, Any], text: str) -> list[WhatsAppLiteAttachment]:
    fallback_caption = None if _is_media_placeholder(text) else (text or None)
    fetched = fetch_external_channel_bridge_attachments(session_key, message_id) or []
    raw_refs = [] if fetched else _raw_media_attachments(raw)
    return _normalize_attachments([*fetched, *raw_refs], fallback_caption=fallback_caption)


def _customer_visible_messages(session_key: str, raw_messages: list[Any]) -> list[WhatsAppLiteMessage]:
    visible: list[WhatsAppLiteMessage] = []
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            continue
        role = (_str(raw.get("role")) or "").lower()
        text = _visible_text(raw)
        message_id = _message_id(raw, index)
        attachments = _message_attachments(session_key, message_id, raw, text)
        if not text and not attachments:
            continue
        author = None
        if role == "user":
            author = "customer"
        elif role == "assistant":
            content = raw.get("content")
            has_tool_content = isinstance(content, list) and any(
                isinstance(item, dict) and item.get("type") in {"toolCall", "toolResult"}
                for item in content
            )
            provider = _str(raw.get("provider"))
            model = _str(raw.get("model"))
            if not has_tool_content and (provider == "external_channel" or model == "delivery-mirror"):
                author = "speedy"
        if author is None:
            continue
        visible.append(
            WhatsAppLiteMessage(
                id=message_id,
                author=author,
                body="图片消息" if attachments and (not text or _is_media_placeholder(text)) else text,
                timestamp=_timestamp(raw.get("timestamp") or raw.get("created_at")),
                attachments=attachments,
            )
        )
    return sorted(visible, key=lambda item: item.timestamp or "")


@router.get("/conversations", response_model=WhatsAppLiteConversationPage)
def list_whatsapp_lite_conversations(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_capability(current_user, CAP_TICKET_READ, db, message="WhatsApp console access denied")
    offset = _offset_from_cursor(cursor)
    upstream_limit = min(500, max(200, (offset + limit) * 4))
    payload = list_external_channel_conversations(limit=upstream_limit, channel="whatsapp")
    conversations = []
    for row in _dedupe_whatsapp_direct_rows(_items(payload)):
        conversation = _conversation_from_row(row)
        if conversation is not None and _matches_conversation_query(conversation, q):
            conversations.append(conversation)
    page_items = conversations[offset:offset + limit]
    next_offset = offset + len(page_items)
    next_cursor = str(next_offset) if next_offset < len(conversations) else None
    return WhatsAppLiteConversationPage(
        items=page_items,
        next_cursor=next_cursor,
        total_visible=len(conversations),
    )


@router.get("/conversation", response_model=WhatsAppLiteConversationDetail)
def get_whatsapp_lite_conversation(
    session_key: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_capability(current_user, CAP_TICKET_READ, db, message="WhatsApp console access denied")
    conversation_payload, messages_payload = read_external_channel_bridge_conversation(session_key, limit=limit)
    if conversation_payload is None or messages_payload is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Legacy WhatsApp Lite source is retired")
    row = conversation_payload if isinstance(conversation_payload, dict) else {"sessionKey": session_key}
    row.setdefault("sessionKey", session_key)
    conversation = _conversation_from_row(row)
    if conversation is None or not _is_whatsapp_direct(row):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp conversation not found")

    related_rows = _related_whatsapp_direct_rows(session_key)
    related_keys = _unique_session_keys(related_rows, session_key)[:40]
    message_groups: list[list[WhatsAppLiteMessage]] = [
        _customer_visible_messages(session_key, _items(messages_payload)),
        _read_outbox_mirror(session_key, limit=limit),
    ]
    for related_key in related_keys:
        if related_key == session_key:
            continue
        _, related_messages_payload = read_external_channel_bridge_conversation(related_key, limit=min(limit, 50))
        if related_messages_payload is None:
            continue
        message_groups.append(_customer_visible_messages(related_key, _items(related_messages_payload)))
        message_groups.append(_read_outbox_mirror(related_key, limit=limit))

    messages = _trim_visible_messages(_merge_visible_messages(*message_groups), limit=limit)
    if messages:
        conversation.latest_message = messages[-1].body
    return WhatsAppLiteConversationDetail(conversation=conversation, messages=messages)


@router.post("/messages", response_model=WhatsAppLiteSendResponse)
def send_whatsapp_lite_message(
    payload: WhatsAppLiteSendRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_capability(current_user, CAP_OUTBOUND_SEND, db, message="WhatsApp sending denied")
    target = _str(payload.recipient)
    account_id = _str(payload.account_id)
    thread_id = _str(payload.thread_id)
    if not target:
        conversation_payload, _ = read_external_channel_bridge_conversation(payload.session_key, limit=1)
        if isinstance(conversation_payload, dict):
            conversation = _conversation_from_row(conversation_payload)
            target = conversation.recipient if conversation else None
            account_id = account_id or (conversation.account_id if conversation else None)
            thread_id = thread_id or (conversation.thread_id if conversation else None)
    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="WhatsApp recipient is missing")
    status_value, provider_status, sent_at = dispatch_via_external_channel_bridge(
        channel="whatsapp",
        target=target,
        body=payload.body.strip(),
        account_id=account_id,
        thread_id=thread_id,
        session_key=payload.session_key,
    )
    ok = status_value == MessageStatus.sent
    if ok:
        operator = _str(getattr(current_user, "display_name", None)) or _str(getattr(current_user, "username", None))
        _append_outbox_mirror(
            session_key=payload.session_key,
            body=payload.body,
            timestamp=sent_at.isoformat() if hasattr(sent_at, "isoformat") else None,
            operator=operator,
        )
    return WhatsAppLiteSendResponse(
        ok=ok,
        status=status_value.value if hasattr(status_value, "value") else str(status_value),
        provider_status=provider_status,
        sent_at=sent_at.isoformat() if hasattr(sent_at, "isoformat") else None,
    )
