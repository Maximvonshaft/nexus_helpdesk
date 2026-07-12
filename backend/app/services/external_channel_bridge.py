from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility, SourceChannel, TicketPriority, TicketSource
from ..models import (
    ChannelAccount,
    ExternalChannelAttachmentReference,
    ExternalChannelConversationLink,
    ExternalChannelSyncCursor,
    ExternalChannelTranscriptMessage,
    ExternalChannelUnresolvedEvent,
    Team,
    Ticket,
    TicketAttachment,
    User,
)
from ..schemas import ExternalChannelConversationRead, ExternalChannelSyncResult, ExternalChannelTranscriptRead
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .observability import LOGGER
from .storage import get_storage_backend


settings = get_settings()

ALLOWED_CHANNEL_ACCOUNT_PROVIDERS = {"whatsapp", "telegram", "sms"}
_RETIRED_STATUS = "legacy_external_channel_runtime_retired"
_ACTIVE_UNRESOLVED_STATUSES = ("pending", "failed", "replaying")


def _retired_dispatch_result(action: str) -> tuple[MessageStatus, str, None]:
    LOGGER.warning(
        "legacy_external_channel_runtime_retired",
        extra={"event_payload": {"action": action, "runtime": "provider_runtime/native_channels"}},
    )
    return MessageStatus.failed, _RETIRED_STATUS, None


def _clean(value: Any, *, limit: int | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _try_extract_attachment_bytes(metadata: dict | None) -> tuple[bytes | None, str | None, str | None]:
    if not isinstance(metadata, dict):
        return None, None, None
    for key in ("base64", "data", "contentBase64"):
        raw = metadata.get(key)
        if isinstance(raw, str) and raw:
            try:
                return (
                    base64.b64decode(raw),
                    _clean(metadata.get("contentType") or metadata.get("mimeType")),
                    _clean(metadata.get("filename") or metadata.get("name"), limit=180),
                )
            except Exception:
                return None, None, None
    text_value = metadata.get("text") or metadata.get("caption")
    if isinstance(text_value, str) and text_value:
        return text_value.encode("utf-8"), "text/plain", _clean(metadata.get("filename") or metadata.get("name"), limit=180) or "attachment.txt"
    return None, None, None


def persist_external_channel_attachment_reference(db: Session, *, attachment_ref: ExternalChannelAttachmentReference) -> Ticket | None:
    ticket = db.query(Ticket).filter(Ticket.id == attachment_ref.ticket_id).first()
    if ticket is None:
        attachment_ref.storage_status = "ticket_missing"
        return None

    raw_bytes, media_type, filename = _try_extract_attachment_bytes(attachment_ref.metadata_json)
    if raw_bytes is None:
        raw_bytes = json.dumps(
            {
                "remote_attachment_id": attachment_ref.remote_attachment_id,
                "content_type": attachment_ref.content_type,
                "filename": attachment_ref.filename,
                "metadata": attachment_ref.metadata_json,
                "legacy_source": "external_channel_attachment_reference",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        filename = (attachment_ref.filename or f"legacy-{attachment_ref.remote_attachment_id}") + ".json"
        media_type = "application/json"

    stored = get_storage_backend().persist_bytes(
        content=raw_bytes,
        filename=filename or attachment_ref.filename or f"legacy-{attachment_ref.remote_attachment_id}",
        media_type=media_type or attachment_ref.content_type or "application/octet-stream",
    )
    attachment = TicketAttachment(
        ticket_id=ticket.id,
        file_name=attachment_ref.filename or filename or f"legacy-{attachment_ref.remote_attachment_id}",
        mime_type=stored.detected_mime_type,
        file_size=stored.size_bytes,
        storage_key=stored.storage_key,
        visibility=NoteVisibility.internal,
        uploaded_by=None,
    )
    db.add(attachment)
    attachment_ref.storage_key = stored.storage_key
    attachment_ref.storage_status = "persisted"
    db.flush()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.external_channel_attachment_persisted,
        note="Legacy inbound attachment reference persisted",
        payload={"attachment_ref_id": attachment_ref.id, "storage_key": stored.storage_key},
    )
    return ticket


def resolve_channel_account(db: Session, *, market_id: int | None, account_id: str | None) -> ChannelAccount | None:
    if account_id:
        row = (
            db.query(ChannelAccount)
            .filter(
                ChannelAccount.account_id == account_id,
                ChannelAccount.provider.in_(ALLOWED_CHANNEL_ACCOUNT_PROVIDERS),
                ChannelAccount.is_active.is_(True),
            )
            .first()
        )
        if row is not None:
            return row
    query = db.query(ChannelAccount).filter(
        ChannelAccount.provider.in_(ALLOWED_CHANNEL_ACCOUNT_PROVIDERS),
        ChannelAccount.is_active.is_(True),
    )
    if market_id is not None:
        market_row = query.filter(ChannelAccount.market_id == market_id).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
        if market_row is not None:
            return market_row
    return query.filter(ChannelAccount.market_id.is_(None)).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()


def _extract_event_session_key(event: dict[str, Any]) -> str | None:
    for container in (event, event.get("message") if isinstance(event.get("message"), dict) else None):
        if not isinstance(container, dict):
            continue
        for key in ("sessionKey", "session_key"):
            value = _clean(container.get(key), limit=255)
            if value:
                return value
    return None


def _extract_event_route(event: dict[str, Any]) -> dict[str, Any]:
    route: dict[str, Any] = {}
    for container in (event, event.get("message") if isinstance(event.get("message"), dict) else None):
        if not isinstance(container, dict):
            continue
        direct_route = container.get("route")
        if isinstance(direct_route, dict):
            route.update({k: v for k, v in direct_route.items() if v not in (None, "")})
        for source, target in (("channel", "channel"), ("recipient", "recipient"), ("to", "recipient"), ("accountId", "accountId"), ("threadId", "threadId")):
            value = container.get(source)
            if value not in (None, "") and target not in route:
                route[target] = value
    return route


def _canonical_external_channel_payload_json(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _external_channel_payload_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_external_channel_payload_json(event).encode("utf-8")).hexdigest()


def _find_active_unresolved_external_channel_event(
    db: Session,
    *,
    source: str,
    session_key: str | None,
    payload_hash: str,
) -> ExternalChannelUnresolvedEvent | None:
    return (
        db.query(ExternalChannelUnresolvedEvent)
        .filter(
            ExternalChannelUnresolvedEvent.source == source,
            func.coalesce(ExternalChannelUnresolvedEvent.session_key, "") == (session_key or ""),
            ExternalChannelUnresolvedEvent.payload_hash == payload_hash,
            ExternalChannelUnresolvedEvent.status.in_(_ACTIVE_UNRESOLVED_STATUSES),
        )
        .order_by(ExternalChannelUnresolvedEvent.id.asc())
        .first()
    )


def _refresh_unresolved_external_channel_event(
    row: ExternalChannelUnresolvedEvent,
    *,
    event_type: str | None,
    recipient: str | None,
    source_chat_id: str | None,
    preferred_reply_contact: str | None,
    error: str | None,
) -> None:
    if event_type is not None:
        row.event_type = event_type
    if recipient is not None:
        row.recipient = recipient
    if source_chat_id is not None:
        row.source_chat_id = source_chat_id
    if preferred_reply_contact is not None:
        row.preferred_reply_contact = preferred_reply_contact
    if error is not None:
        row.last_error = error
    row.updated_at = utc_now()


def persist_unresolved_external_channel_event(
    db: Session,
    *,
    event: dict[str, Any],
    source: str = "legacy",
    session_key: str | None = None,
    error: str | None = None,
) -> ExternalChannelUnresolvedEvent:
    route = _extract_event_route(event)
    resolved_session_key = session_key or _extract_event_session_key(event)
    event_type = _clean(event.get("type") or event.get("event_type"), limit=80)
    recipient = _clean(route.get("recipient"), limit=255)
    source_chat_id = _clean(route.get("threadId") or route.get("source_chat_id"), limit=120)
    preferred_reply_contact = _clean(route.get("recipient"), limit=160)
    payload_json = _canonical_external_channel_payload_json(event)
    payload_hash = _external_channel_payload_hash(event)

    existing = _find_active_unresolved_external_channel_event(
        db,
        source=source,
        session_key=resolved_session_key,
        payload_hash=payload_hash,
    )
    if existing is not None:
        _refresh_unresolved_external_channel_event(
            existing,
            event_type=event_type,
            recipient=recipient,
            source_chat_id=source_chat_id,
            preferred_reply_contact=preferred_reply_contact,
            error=error,
        )
        db.flush()
        return existing

    row = ExternalChannelUnresolvedEvent(
        source=source,
        session_key=resolved_session_key,
        event_type=event_type,
        recipient=recipient,
        source_chat_id=source_chat_id,
        preferred_reply_contact=preferred_reply_contact,
        payload_json=payload_json,
        payload_hash=payload_hash,
        status="pending",
        replay_count=0,
        last_error=error,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        winner = _find_active_unresolved_external_channel_event(
            db,
            source=source,
            session_key=resolved_session_key,
            payload_hash=payload_hash,
        )
        if winner is None:
            raise
        _refresh_unresolved_external_channel_event(
            winner,
            event_type=event_type,
            recipient=recipient,
            source_chat_id=source_chat_id,
            preferred_reply_contact=preferred_reply_contact,
            error=error,
        )
        db.flush()
        return winner


def process_external_channel_inbound_event(
    db: Session,
    *,
    event: dict[str, Any],
    source: str = "legacy",
    client: Any | None = None,
) -> bool:
    persist_unresolved_external_channel_event(
        db,
        event=event,
        source=source,
        error="legacy_external_channel_ingest_retired",
    )
    return False


def replay_unresolved_external_channel_event(db: Session, *, row: ExternalChannelUnresolvedEvent) -> bool:
    row.replay_count += 1
    row.status = "failed"
    row.last_error = "legacy_external_channel_replay_retired"
    row.updated_at = utc_now()
    db.flush()
    return False


def set_conversation_state(db: Session, *, ticket: Ticket, new_state, actor_id: int | None = None, note: str | None = None) -> None:
    ticket.conversation_state = new_state
    ticket.updated_at = utc_now()
    db.flush()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=actor_id,
        event_type=EventType.field_updated,
        note=note or "Conversation state updated",
        payload={"field": "conversation_state", "new_value": getattr(new_state, "value", str(new_state))},
    )


def upsert_external_channel_sync_cursor(db: Session, *, source: str, cursor_value: str | None) -> ExternalChannelSyncCursor:
    row = db.query(ExternalChannelSyncCursor).filter(ExternalChannelSyncCursor.source == source).first()
    if row is None:
        row = ExternalChannelSyncCursor(source=source, cursor_value=cursor_value)
        db.add(row)
    else:
        row.cursor_value = cursor_value
        row.updated_at = utc_now()
    db.flush()
    return row


def pick_team_for_market(db: Session, *, market_id: int | None = None, country_code: str | None = None) -> Team | None:
    query = db.query(Team).filter(Team.is_active.is_(True), Team.team_type == "support")
    if market_id is not None:
        row = query.filter(Team.market_id == market_id).order_by(Team.id.asc()).first()
        if row is not None:
            return row
    return query.filter(Team.market_id.is_(None)).order_by(Team.id.asc()).first()


def _get_external_channel_ticket_actor(db: Session) -> User | None:
    return db.query(User).filter(User.username == "system").first() or db.query(User).order_by(User.id.asc()).first()


def ensure_external_channel_conversation_link(
    db: Session,
    *,
    ticket: Ticket,
    session_key: str,
    channel: str | None = None,
    recipient: str | None = None,
    account_id: str | None = None,
    thread_id: str | None = None,
    route: dict[str, Any] | None = None,
) -> ExternalChannelConversationLink:
    row = db.query(ExternalChannelConversationLink).filter(ExternalChannelConversationLink.session_key == session_key).first()
    if row is None:
        row = ExternalChannelConversationLink(ticket_id=ticket.id, session_key=session_key)
        db.add(row)
    row.ticket_id = ticket.id
    row.channel = _clean(channel or _as_dict(route).get("channel"), limit=80)
    row.recipient = _clean(recipient or _as_dict(route).get("recipient"), limit=255)
    row.account_id = _clean(account_id or _as_dict(route).get("accountId"), limit=160)
    row.thread_id = _clean(thread_id or _as_dict(route).get("threadId"), limit=160)
    account = resolve_channel_account(db, market_id=ticket.market_id, account_id=row.account_id)
    if account is not None:
        row.channel_account_id = account.id
    row.updated_at = utc_now()
    db.flush()
    return row


def link_ticket_to_external_channel_session(
    db: Session,
    *,
    ticket_id: int,
    session_key: str,
    channel: str | None = None,
    recipient: str | None = None,
    account_id: str | None = None,
    thread_id: str | None = None,
    route: dict[str, Any] | None = None,
) -> ExternalChannelConversationLink:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise ValueError("ticket_not_found")
    return ensure_external_channel_conversation_link(
        db,
        ticket=ticket,
        session_key=session_key,
        channel=channel,
        recipient=recipient,
        account_id=account_id,
        thread_id=thread_id,
        route=route,
    )


def _sync_result_for_link(db: Session, link: ExternalChannelConversationLink) -> ExternalChannelSyncResult:
    messages = (
        db.query(ExternalChannelTranscriptMessage)
        .filter(ExternalChannelTranscriptMessage.conversation_id == link.id)
        .order_by(ExternalChannelTranscriptMessage.received_at.asc(), ExternalChannelTranscriptMessage.id.asc())
        .all()
    )
    return ExternalChannelSyncResult(
        conversation=ExternalChannelConversationRead.model_validate(link),
        messages=[ExternalChannelTranscriptRead.model_validate(row) for row in messages],
        linked_ticket_id=link.ticket_id,
    )


def sync_external_channel_conversation(
    db: Session,
    *,
    ticket_id: int,
    session_key: str,
    limit: int = 50,
    client: Any | None = None,
) -> ExternalChannelSyncResult:
    link = link_ticket_to_external_channel_session(db, ticket_id=ticket_id, session_key=session_key)
    link.last_synced_at = utc_now()
    link.updated_at = utc_now()
    db.flush()
    return _sync_result_for_link(db, link)


def count_stale_external_channel_links(db: Session) -> int:
    if not settings.external_channel_sync_enabled:
        return 0
    cutoff = utc_now() - timedelta(seconds=settings.external_channel_sync_stale_seconds)
    return (
        db.query(ExternalChannelConversationLink)
        .filter(
            ExternalChannelConversationLink.session_key.is_not(None),
            (ExternalChannelConversationLink.last_synced_at.is_(None)) | (ExternalChannelConversationLink.last_synced_at < cutoff),
        )
        .count()
    )


def list_stale_external_channel_links(db: Session, *, limit: int | None = None) -> list[ExternalChannelConversationLink]:
    if not settings.external_channel_sync_enabled:
        return []
    cutoff = utc_now() - timedelta(seconds=settings.external_channel_sync_stale_seconds)
    query = (
        db.query(ExternalChannelConversationLink)
        .filter(
            ExternalChannelConversationLink.session_key.is_not(None),
            (ExternalChannelConversationLink.last_synced_at.is_(None)) | (ExternalChannelConversationLink.last_synced_at < cutoff),
        )
        .order_by(ExternalChannelConversationLink.last_synced_at.asc().nullsfirst(), ExternalChannelConversationLink.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def serialize_external_channel_link(link: ExternalChannelConversationLink) -> ExternalChannelConversationRead:
    return ExternalChannelConversationRead.model_validate(link)


def list_external_channel_bridge_conversations(*, limit: int = 50, channel: str | None = None) -> dict[str, Any]:
    return {"conversations": [], "degraded": True, "degraded_reason": "legacy_external_channel_bridge_retired"}


def list_external_channel_conversations(*, limit: int = 50, channel: str | None = None, client: Any | None = None) -> dict[str, Any]:
    return list_external_channel_bridge_conversations(limit=limit, channel=channel)


def sync_external_channel_inbound_conversations_once(
    db: Session,
    *,
    source: str = "default",
    limit: int | None = None,
    client: Any | None = None,
) -> dict[str, int | str]:
    return {
        "status": "disabled",
        "reason": "legacy_external_channel_inbound_retired",
        "conversations_seen": 0,
        "synced_conversations": 0,
        "tickets_created": 0,
        "messages_inserted": 0,
        "unresolved_events": 0,
    }


def read_external_channel_bridge_conversation(session_key: str, limit: int = 50) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    return None, None


def fetch_external_channel_bridge_attachments(session_key: str, message_id: str) -> list[dict[str, Any]] | None:
    return []


def dispatch_via_external_channel_bridge(
    *,
    channel: str,
    target: str,
    body: str,
    account_id: str | None = None,
    thread_id: str | None = None,
    session_key: str | None = None,
) -> tuple[MessageStatus, str | None, object | None]:
    return _retired_dispatch_result("dispatch_bridge")


def dispatch_via_external_channel_mcp(session_key: str, body: str) -> tuple[MessageStatus, str | None, object | None]:
    return _retired_dispatch_result("dispatch_mcp")


def dispatch_via_external_channel_cli(
    *,
    channel: str,
    target: str,
    body: str,
    account_id: str | None = None,
    thread_id: str | None = None,
) -> tuple[MessageStatus, str | None, object | None]:
    return _retired_dispatch_result("dispatch_cli")


def sync_external_channel_session_once(
    db: Session,
    *,
    link: ExternalChannelConversationLink,
    limit: int | None = None,
    client: Any | None = None,
) -> ExternalChannelSyncResult:
    return sync_external_channel_conversation(
        db,
        ticket_id=link.ticket_id,
        session_key=link.session_key,
        limit=limit or settings.external_channel_sync_transcript_limit,
        client=client,
    )


def wait_external_channel_bridge_events(after_cursor: int, session_key: str | None = None, timeout_seconds: int = 30) -> dict[str, Any] | None:
    return None


def poll_external_channel_bridge_events(after_cursor: int, session_key: str | None = None) -> dict[str, Any] | None:
    return None


def consume_external_channel_events_once(db: Session, *, source: str = "default", timeout_seconds: int | None = None) -> int:
    return 0
