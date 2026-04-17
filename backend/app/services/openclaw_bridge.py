from __future__ import annotations

import base64
import ipaddress
import json
import socket
import subprocess
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, MessageStatus, NoteVisibility
from ..models import ChannelAccount, OpenClawAttachmentReference, OpenClawConversationLink, OpenClawSyncCursor, OpenClawTranscriptMessage, Team, Ticket, TicketAttachment
from ..schemas import OpenClawConversationRead, OpenClawSyncResult, OpenClawTranscriptRead
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .observability import LOGGER
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError
from .storage import get_storage_backend


settings = get_settings()


def _is_public_ip_address(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not any([
        addr.is_private,
        addr.is_loopback,
        addr.is_link_local,
        addr.is_multicast,
        addr.is_reserved,
        addr.is_unspecified,
    ])


def _host_matches_allowlist(hostname: str) -> bool:
    allowed_hosts = [host.lower() for host in settings.openclaw_attachment_allowed_hosts]
    return any(hostname == host or hostname.endswith(f'.{host}') for host in allowed_hosts)


def _resolved_host_is_public(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = {item[4][0] for item in infos if item and item[4]}
    return bool(addresses) and all(_is_public_ip_address(address) for address in addresses)


def _read_bounded_response(resp) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    max_bytes = settings.openclaw_attachment_max_download_bytes
    while True:
        chunk = resp.read(min(65536, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b''.join(chunks)


def _try_fetch_remote_attachment(url: str, metadata: dict[str, Any]) -> tuple[bytes | None, str | None, str | None]:
    if not settings.openclaw_attachment_url_fetch_enabled:
        return None, None, None
    try:
        parsed = urlparse(url)
    except Exception:
        return None, None, None
    if parsed.scheme.lower() != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return None, None, None
    hostname = parsed.hostname.lower()
    if not _host_matches_allowlist(hostname):
        return None, None, None
    if not _resolved_host_is_public(hostname):
        return None, None, None
    request = urllib.request.Request(url, headers={'User-Agent': 'helpdesk-suite/attachment-fetch'})
    try:
        with urllib.request.urlopen(request, timeout=settings.openclaw_attachment_fetch_timeout_seconds) as resp:
            media_type = metadata.get('contentType') or metadata.get('mimeType') or resp.headers.get_content_type()
            if media_type not in settings.openclaw_attachment_allowed_mime_types:
                return None, None, None
            content = _read_bounded_response(resp)
            if content is None:
                return None, None, None
            return content, media_type, metadata.get('filename') or metadata.get('name')
    except Exception:
        return None, None, None


def _try_extract_attachment_bytes(metadata: dict | None) -> tuple[bytes | None, str | None, str | None]:
    if not isinstance(metadata, dict):
        return None, None, None
    for key in ("base64", "data", "contentBase64"):
        raw = metadata.get(key)
        if isinstance(raw, str) and raw:
            try:
                return base64.b64decode(raw), metadata.get("contentType") or metadata.get("mimeType"), metadata.get("filename") or metadata.get("name")
            except Exception:
                pass
    for key in ("downloadUrl", "url"):
        url = metadata.get(key)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            fetched = _try_fetch_remote_attachment(url, metadata)
            if fetched[0] is not None:
                return fetched
    text_value = metadata.get("text") or metadata.get("caption")
    if isinstance(text_value, str) and text_value:
        return text_value.encode("utf-8"), "text/plain", metadata.get("filename") or metadata.get("name") or "attachment.txt"
    return None, None, None


def _extract_event_session_key(event: dict[str, Any]) -> str | None:
    for key in ("sessionKey", "session_key"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    message = event.get("message")
    if isinstance(message, dict):
        for key in ("sessionKey", "session_key"):
            value = message.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def persist_openclaw_attachment_reference(db: Session, *, attachment_ref: OpenClawAttachmentReference) -> Ticket | None:
    storage = get_storage_backend()
    ticket = db.query(Ticket).filter(Ticket.id == attachment_ref.ticket_id).first()
    if ticket is None:
        attachment_ref.storage_status = "ticket_missing"
        return None
    raw_bytes, media_type, filename = _try_extract_attachment_bytes(attachment_ref.metadata_json)
    if raw_bytes is None:
        payload = json.dumps(
            {
                "remote_attachment_id": attachment_ref.remote_attachment_id,
                "content_type": attachment_ref.content_type,
                "filename": attachment_ref.filename,
                "metadata": attachment_ref.metadata_json,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        filename = (attachment_ref.filename or f"openclaw-{attachment_ref.remote_attachment_id}") + ".json"
        media_type = "application/json"
        raw_bytes = payload
    stored = storage.persist_bytes(content=raw_bytes, filename=filename or (attachment_ref.filename or f"openclaw-{attachment_ref.remote_attachment_id}"), media_type=media_type or attachment_ref.content_type or "application/octet-stream")
    attachment_ref.storage_key = stored.storage_key
    attachment_ref.storage_status = "captured"
    existing = db.query(TicketAttachment).filter(
        TicketAttachment.ticket_id == ticket.id,
        TicketAttachment.storage_key == stored.storage_key,
    ).first()
    if existing is None:
        file_name = attachment_ref.filename or f"openclaw-{attachment_ref.remote_attachment_id}.json"
        ticket_attachment = TicketAttachment(
            ticket_id=ticket.id,
            uploaded_by=ticket.created_by,
            file_name=file_name,
            storage_key=stored.storage_key,
            file_path=str(stored.absolute_path) if stored.absolute_path else None,
            file_url=None,
            mime_type=media_type or attachment_ref.content_type or "application/octet-stream",
            file_size=stored.size_bytes,
            visibility=NoteVisibility.internal,
        )
        db.add(ticket_attachment)
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=ticket.created_by,
        event_type=EventType.openclaw_attachment_persisted,
        note="OpenClaw attachment metadata captured into ticket storage",
        payload={"attachment_id": attachment_ref.remote_attachment_id, "storage_key": attachment_ref.storage_key},
    )
    return ticket


def _extract_attachment_refs(payload: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("attachments", "items", "results", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            items = [payload]
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        attachment_id = item.get("attachmentId") or item.get("id") or item.get("blobId")
        if not attachment_id:
            continue
        refs.append({
            "remote_attachment_id": str(attachment_id),
            "content_type": item.get("contentType") or item.get("mimeType") or item.get("type"),
            "filename": item.get("filename") or item.get("name"),
            "metadata_json": item,
        })
    return refs


def resolve_channel_account(db: Session, *, market_id: int | None, account_id: str | None) -> ChannelAccount | None:
    if account_id:
        row = db.query(ChannelAccount).filter(ChannelAccount.account_id == account_id, ChannelAccount.is_active.is_(True)).first()
        if row:
            return row
    q = db.query(ChannelAccount).filter(ChannelAccount.is_active.is_(True))
    if market_id is not None:
        row = q.filter(ChannelAccount.market_id == market_id).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
        if row:
            return row
    return q.order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()


def set_conversation_state(db: Session, *, ticket: Ticket, new_state, actor_id: int | None = None, note: str | None = None) -> None:
    if ticket.conversation_state == new_state:
        return
    old = ticket.conversation_state
    ticket.conversation_state = new_state
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=actor_id,
        event_type=EventType.conversation_state_changed,
        old_value=old.value if old else None,
        new_value=new_state.value if hasattr(new_state, "value") else str(new_state),
        note=note,
    )


def upsert_openclaw_sync_cursor(db: Session, *, source: str, cursor_value: str | None) -> OpenClawSyncCursor:
    row = db.query(OpenClawSyncCursor).filter(OpenClawSyncCursor.source == source).first()
    if row is None:
        row = OpenClawSyncCursor(source=source, cursor_value=cursor_value)
        db.add(row)
        db.flush()
    else:
        row.cursor_value = cursor_value
    row.updated_at = utc_now()
    return row


def pick_team_for_market(db: Session, *, market_id: int | None = None, country_code: str | None = None) -> Team | None:
    q = db.query(Team).filter(Team.is_active.is_(True))
    if market_id is not None:
        team = q.filter(Team.market_id == market_id).order_by(Team.id.asc()).first()
        if team:
            return team
    if country_code:
        team = q.join(Team.market).filter_by(country_code=country_code.upper()).order_by(Team.id.asc()).first()
        if team:
            return team
    return q.filter(Team.team_type == 'support').order_by(Team.id.asc()).first()


def _as_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('conversations', 'items', 'messages', 'results'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _normalize_message_body(message: dict[str, Any]) -> str | None:
    for key in ('text', 'body', 'message', 'contentText'):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = message.get('content')
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict):
                text = block.get('text') or block.get('content')
                if isinstance(text, str) and text.strip():
                    pieces.append(text.strip())
        if pieces:
            return '\n'.join(pieces)
    return None


def _extract_route(conversation: dict[str, Any]) -> dict[str, Any]:
    payload = conversation.get('conversation') if isinstance(conversation.get('conversation'), dict) else conversation
    route = payload.get('route') if isinstance(payload.get('route'), dict) else {}
    for key, route_key in [('channel', 'channel'), ('recipient', 'recipient'), ('accountId', 'accountId'), ('threadId', 'threadId'), ('to', 'recipient')]:
        if key in payload and route_key not in route:
            route[route_key] = payload.get(key)
    return route


def _extract_message_id(message: dict[str, Any]) -> str | None:
    for key in ('message_id', 'messageId', 'id'):
        value = message.get(key)
        if value not in (None, ''):
            return str(value)
    meta = message.get('__openclaw')
    if isinstance(meta, dict):
        for key in ('id', 'message_id', 'messageId'):
            value = meta.get(key)
            if value not in (None, ''):
                return str(value)
    return None


def _should_sync_transcript_message(*, role: Any, body_text: str | None, attachment_refs: list[dict[str, Any]]) -> bool:
    normalized_role = str(role or '').lower()
    if normalized_role not in {'user', 'assistant'}:
        return False
    return bool(body_text or attachment_refs)


def ensure_openclaw_conversation_link(db: Session, *, ticket: Ticket, session_key: str, channel: str | None = None, recipient: str | None = None, account_id: str | None = None, thread_id: str | None = None, route: dict[str, Any] | None = None) -> OpenClawConversationLink:
    link = db.query(OpenClawConversationLink).filter(OpenClawConversationLink.ticket_id == ticket.id).first()
    if link is None:
        link = OpenClawConversationLink(ticket_id=ticket.id, session_key=session_key)
        db.add(link)
        db.flush()
    link.session_key = session_key
    link.channel = channel or link.channel
    link.recipient = recipient or link.recipient
    link.account_id = account_id or link.account_id
    link.thread_id = thread_id or link.thread_id
    if route:
        link.route_json = route
        link.channel = route.get('channel') or link.channel
        link.recipient = route.get('recipient') or link.recipient
        link.account_id = route.get('accountId') or link.account_id
        link.thread_id = route.get('threadId') or link.thread_id
    channel_account = resolve_channel_account(db, market_id=ticket.market_id, account_id=link.account_id)
    if channel_account is not None:
        link.channel_account_id = channel_account.id
        ticket.channel_account_id = channel_account.id
    link.updated_at = utc_now()
    if link.channel and not ticket.preferred_reply_channel:
        ticket.preferred_reply_channel = link.channel
    if link.recipient and not ticket.preferred_reply_contact:
        ticket.preferred_reply_contact = link.recipient
    set_conversation_state(db, ticket=ticket, new_state=ticket.conversation_state, note=None)
    return link


def link_ticket_to_openclaw_session(db: Session, *, ticket_id: int, session_key: str, channel: str | None = None, recipient: str | None = None, account_id: str | None = None, thread_id: str | None = None, route: dict[str, Any] | None = None) -> OpenClawConversationLink:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail='Ticket not found')
    link = ensure_openclaw_conversation_link(db, ticket=ticket, session_key=session_key, channel=channel, recipient=recipient, account_id=account_id, thread_id=thread_id, route=route)
    db.flush()
    return link


def sync_openclaw_conversation(db: Session, *, ticket_id: int, session_key: str, limit: int = 50, client: OpenClawMCPClient | None = None) -> OpenClawSyncResult:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail='Ticket not found')

    synced_rows = []

    def _run_sync(active_client: OpenClawMCPClient | None):
        nonlocal synced_rows
        
        conversation_payload = None
        messages_payload = None
        bridge_success = False

        if settings.openclaw_bridge_enabled:
            conversation_payload, messages_payload = read_openclaw_bridge_conversation(session_key, limit=limit)
            if conversation_payload is not None and messages_payload is not None:
                bridge_success = True
                LOGGER.info('openclaw_bridge_read_success', extra={'event_payload': {'session_key': session_key, 'messages_count': len(messages_payload)}})
            else:
                LOGGER.warning('openclaw_bridge_read_fallback', extra={'event_payload': {'session_key': session_key, 'reason': 'bridge_failed_or_missing_data'}})
        
        if not bridge_success:
            if not active_client:
                raise RuntimeError("bridge read failed and no active MCP client provided for fallback")
            LOGGER.info('openclaw_mcp_read_invoked', extra={'event_payload': {'session_key': session_key}})
            try:
                conversation_payload = active_client.conversation_get(session_key)
                messages_payload = active_client.messages_read(session_key, limit=limit)
            except Exception as e:
                LOGGER.warning('openclaw_mcp_read_failed', extra={'event_payload': {'session_key': session_key, 'error': str(e)}})
                raise e

        conversation = conversation_payload if isinstance(conversation_payload, dict) else {}
        route = _extract_route(conversation)
        link = ensure_openclaw_conversation_link(db, ticket=ticket, session_key=session_key, route=route)

        for message in _as_items(messages_payload):
            message_id = _extract_message_id(message)
            if not message_id:
                continue
            body_text = _normalize_message_body(message)
            role = message.get('role') or message.get('senderRole') or message.get('authorRole')
            author_name = message.get('author_name') or message.get('author') or message.get('sender')
            attachments_payload = None
            
            # Bridge primary path
            if bridge_success and settings.openclaw_bridge_enabled:
                fetched = fetch_openclaw_bridge_attachments(session_key, message_id)
                if fetched is not None:
                    attachments_payload = fetched
                    LOGGER.info('openclaw_bridge_read_success', extra={'event_payload': {'action': 'attachments_fetch', 'session_key': session_key, 'message_id': message_id, 'attachments_count': len(fetched)}})
                else:
                    LOGGER.warning('openclaw_bridge_read_fallback', extra={'event_payload': {'action': 'attachments_fetch', 'session_key': session_key, 'message_id': message_id}})

            # Fallback to MCP
            if attachments_payload is None and active_client:
                LOGGER.info('openclaw_mcp_read_invoked', extra={'event_payload': {'action': 'attachments_fetch', 'session_key': session_key, 'message_id': message_id}})
                try:
                    attachments_payload = active_client.attachments_fetch(message_id)
                except Exception as e:
                    LOGGER.warning('openclaw_mcp_read_failed', extra={'event_payload': {'action': 'attachments_fetch', 'session_key': session_key, 'message_id': message_id, 'error': str(e)}})
                    attachments_payload = None
                    
            attachment_refs = _extract_attachment_refs(attachments_payload)
            if not _should_sync_transcript_message(role=role, body_text=body_text, attachment_refs=attachment_refs):
                continue
            row = db.query(OpenClawTranscriptMessage).filter(
                OpenClawTranscriptMessage.conversation_id == link.id,
                OpenClawTranscriptMessage.message_id == message_id,
            ).first()
            if row is None:
                row = OpenClawTranscriptMessage(
                    conversation_id=link.id,
                    ticket_id=ticket.id,
                    session_key=session_key,
                    message_id=message_id,
                    role=role,
                    author_name=author_name,
                    body_text=body_text,
                    content_json=message,
                    received_at=utc_now(),
                )
                db.add(row)
                db.flush()
            else:
                row.role = role
                row.author_name = author_name
                row.body_text = body_text
                row.content_json = message
            synced_rows.append(row)
            if body_text and str(role).lower() == 'user':
                ticket.last_customer_message = body_text
                if ticket.conversation_state.value in {'replied_to_customer', 'waiting_customer'}:
                    set_conversation_state(db, ticket=ticket, new_state=type(ticket.conversation_state).reopened_by_customer, note='Customer replied after prior response')
                else:
                    set_conversation_state(db, ticket=ticket, new_state=type(ticket.conversation_state).ai_active, note='Customer message synchronized from OpenClaw')

            for ref in attachment_refs:
                existing = db.query(OpenClawAttachmentReference).filter(
                    OpenClawAttachmentReference.transcript_message_id == row.id,
                    OpenClawAttachmentReference.remote_attachment_id == ref['remote_attachment_id'],
                ).first()
                if existing is None:
                    attachment_ref = OpenClawAttachmentReference(
                        ticket_id=ticket.id,
                        conversation_id=link.id,
                        transcript_message_id=row.id,
                        remote_attachment_id=ref['remote_attachment_id'],
                        content_type=ref.get('content_type'),
                        filename=ref.get('filename'),
                        metadata_json=ref.get('metadata_json'),
                        storage_status='referenced',
                    )
                    db.add(attachment_ref)
                    db.flush()
                    from .background_jobs import enqueue_attachment_persist_job
                    enqueue_attachment_persist_job(db, attachment_ref_id=attachment_ref.id, dedupe=True)
                    log_event(
                        db,
                        ticket_id=ticket.id,
                        actor_id=ticket.created_by,
                        event_type=EventType.openclaw_attachment_synced,
                        note='OpenClaw attachment reference synchronized',
                        payload={'message_id': message_id, 'attachment_id': ref['remote_attachment_id']},
                    )
        return link

    if client is None:
        with OpenClawMCPClient() as managed_client:
            link = _run_sync(managed_client)
    else:
        link = _run_sync(client)

    if synced_rows:
        link.last_message_id = synced_rows[-1].message_id
    link.last_synced_at = utc_now()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=ticket.created_by,
        event_type=EventType.openclaw_synced,
        note='OpenClaw conversation synchronized',
        payload={'session_key': session_key, 'messages_synced': len(synced_rows)},
    )
    db.flush()
    db.refresh(link)
    return OpenClawSyncResult(
        conversation=OpenClawConversationRead.model_validate(link),
        messages=[OpenClawTranscriptRead.model_validate(item) for item in synced_rows],
        linked_ticket_id=ticket.id,
    )

def count_stale_openclaw_links(db: Session) -> int:
    cutoff = utc_now() - timedelta(seconds=settings.openclaw_sync_stale_seconds)
    query = db.query(OpenClawConversationLink).join(OpenClawConversationLink.ticket).filter(
        Ticket.status.notin_(['closed', 'canceled']),
    )
    query = query.filter(
        (OpenClawConversationLink.last_synced_at.is_(None)) | (OpenClawConversationLink.last_synced_at < cutoff)
    )
    return query.count()


def list_stale_openclaw_links(db: Session, *, limit: int | None = None) -> list[OpenClawConversationLink]:
    limit = limit or settings.openclaw_sync_batch_size
    cutoff = utc_now() - timedelta(seconds=settings.openclaw_sync_stale_seconds)
    query = db.query(OpenClawConversationLink).join(OpenClawConversationLink.ticket).filter(
        Ticket.status.notin_(['closed', 'canceled']),
    )
    query = query.filter(
        (OpenClawConversationLink.last_synced_at.is_(None)) | (OpenClawConversationLink.last_synced_at < cutoff)
    )
    return query.order_by(OpenClawConversationLink.last_synced_at.asc().nullsfirst(), OpenClawConversationLink.id.asc()).limit(limit).all()


def serialize_openclaw_link(link: OpenClawConversationLink) -> OpenClawConversationRead:
    return OpenClawConversationRead.model_validate(link)


def read_openclaw_bridge_conversation(session_key: str, limit: int = 50) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    if not settings.openclaw_bridge_enabled:
        return None, None
    bridge_url = settings.openclaw_bridge_url.rstrip('/')
    
    conversation = None
    messages = None
    
    # fetch conversation
    try:
        req_conv = urllib.request.Request(
            f'{bridge_url}/conversation-get',
            data=json.dumps({'sessionKey': session_key}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req_conv, timeout=settings.openclaw_bridge_timeout_seconds) as resp:
            parsed = json.loads(resp.read().decode('utf-8'))
            if parsed.get('ok'):
                conversation = parsed.get('conversation')
    except Exception as exc:
        LOGGER.warning('openclaw_bridge_read_failed', extra={'event_payload': {'action': 'conversation_get', 'session_key': session_key, 'error': str(exc)}})
        return None, None

    # fetch messages
    try:
        req_msg = urllib.request.Request(
            f'{bridge_url}/read-messages',
            data=json.dumps({'sessionKey': session_key, 'limit': limit}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req_msg, timeout=settings.openclaw_bridge_timeout_seconds) as resp:
            parsed = json.loads(resp.read().decode('utf-8'))
            if parsed.get('ok'):
                messages = parsed.get('messages')
    except Exception as exc:
        LOGGER.warning('openclaw_bridge_read_failed', extra={'event_payload': {'action': 'messages_read', 'session_key': session_key, 'error': str(exc)}})
        return None, None
        
    return conversation, messages


def fetch_openclaw_bridge_attachments(session_key: str, message_id: str) -> list[dict[str, Any]] | None:
    if not settings.openclaw_bridge_enabled:
        return None
    bridge_url = settings.openclaw_bridge_url.rstrip('/')
    try:
        req = urllib.request.Request(
            f'{bridge_url}/attachments-fetch',
            data=json.dumps({'sessionKey': session_key, 'messageId': message_id}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=settings.openclaw_bridge_timeout_seconds) as resp:
            parsed = json.loads(resp.read().decode('utf-8'))
            if parsed.get('ok'):
                return parsed.get('attachments')
    except Exception as exc:
        LOGGER.warning('openclaw_bridge_read_failed', extra={'event_payload': {'action': 'attachments_fetch', 'session_key': session_key, 'message_id': message_id, 'error': str(exc)}})
    return None

def dispatch_via_openclaw_bridge(
    *,
    channel: str,
    target: str,
    body: str,
    account_id: str | None = None,
    thread_id: str | None = None,
    session_key: str | None = None,
) -> tuple[MessageStatus, str | None, object | None]:
    if not settings.openclaw_bridge_enabled:
        return MessageStatus.failed, 'OPENCLAW_BRIDGE_ENABLED is false', None
    bridge_url = settings.openclaw_bridge_url.rstrip('/')
    payload: dict[str, Any] = {
        'channel': channel,
        'target': target,
        'body': body,
    }
    if account_id:
        payload['accountId'] = account_id
    if thread_id:
        payload['threadId'] = thread_id
    if session_key:
        payload['sessionKey'] = session_key
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        f'{bridge_url}/send-message',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.openclaw_bridge_timeout_seconds) as resp:
            raw = resp.read().decode('utf-8')
            parsed = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        response_body = ''
        try:
            response_body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            response_body = ''
        LOGGER.warning(
            'openclaw_bridge_dispatch_failed',
            extra={'event_payload': {
                'dispatch': 'bridge',
                'channel': channel,
                'target': target,
                'status_code': exc.code,
                'response_body': response_body[:500],
            }},
        )
        return MessageStatus.failed, f'openclaw_bridge_http_{exc.code}', None
    except Exception as exc:
        LOGGER.warning(
            'openclaw_bridge_dispatch_failed',
            extra={'event_payload': {
                'dispatch': 'bridge',
                'channel': channel,
                'target': target,
                'error': str(exc),
            }},
        )
        return MessageStatus.failed, f'openclaw_bridge_error: {exc}', None

    if not isinstance(parsed, dict) or not parsed.get('ok'):
        error_message = None
        if isinstance(parsed, dict):
            error_message = parsed.get('error') or parsed.get('message')
        LOGGER.warning(
            'openclaw_bridge_dispatch_rejected',
            extra={'event_payload': {
                'dispatch': 'bridge',
                'channel': channel,
                'target': target,
                'response': parsed if isinstance(parsed, dict) else {'raw': str(parsed)},
            }},
        )
        return MessageStatus.failed, f'openclaw_bridge_gateway_error: {error_message or "unknown"}', None

    LOGGER.info(
        'openclaw_bridge_dispatch_success',
        extra={'event_payload': {
            'dispatch': 'bridge',
            'channel': channel,
            'target': target,
            'bridge_request_id': parsed.get('bridgeRequestId'),
            'gateway_message_id': (parsed.get('result') or {}).get('messageId') if isinstance(parsed.get('result'), dict) else None,
        }},
    )
    return MessageStatus.sent, 'sent_via_openclaw_bridge', utc_now()


def dispatch_via_openclaw_mcp(session_key: str, body: str) -> tuple[MessageStatus, str | None, object | None]:
    try:
        with OpenClawMCPClient() as client:
            client.messages_send(session_key, body)
        return MessageStatus.sent, 'sent_via_openclaw_mcp', utc_now()
    except Exception as exc:
        return MessageStatus.failed, f'openclaw_mcp_error: {exc}', None


def dispatch_via_openclaw_cli(*, channel: str, target: str, body: str, account_id: str | None = None, thread_id: str | None = None) -> tuple[MessageStatus, str | None, object | None]:
    openclaw_bin = settings.openclaw_bin
    if not openclaw_bin:
        return MessageStatus.failed, 'OPENCLAW_BIN is not configured', None
    cmd = [openclaw_bin, 'message', 'send', '--channel', channel, '--target', target, '--message', body]
    if account_id:
        cmd.extend(['--account', account_id])
    if thread_id:
        cmd.extend(['--thread-id', thread_id])
    LOGGER.warning(
        'openclaw_cli_fallback_invoked',
        extra={'event_payload': {
            'dispatch': 'cli_fallback',
            'channel': channel,
            'target': target,
            'command': cmd,
        }},
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        LOGGER.info(
            'openclaw_cli_fallback_success',
            extra={'event_payload': {
                'dispatch': 'cli_fallback',
                'channel': channel,
                'target': target,
            }},
        )
        return MessageStatus.sent, 'sent_via_openclaw_cli_fallback', utc_now()
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()[:500] if exc.stderr else 'openclaw send failed'
        LOGGER.warning(
            'openclaw_cli_fallback_failed',
            extra={'event_payload': {
                'dispatch': 'cli_fallback',
                'channel': channel,
                'target': target,
                'error': stderr,
            }},
        )
        return MessageStatus.failed, stderr, None
    except Exception as exc:
        LOGGER.warning(
            'openclaw_cli_fallback_failed',
            extra={'event_payload': {
                'dispatch': 'cli_fallback',
                'channel': channel,
                'target': target,
                'error': str(exc),
            }},
        )
        return MessageStatus.failed, str(exc), None


def sync_openclaw_session_once(db: Session, *, link: OpenClawConversationLink, limit: int | None = None, client: OpenClawMCPClient | None = None) -> OpenClawSyncResult:
    return sync_openclaw_conversation(
        db,
        ticket_id=link.ticket_id,
        session_key=link.session_key,
        limit=limit or settings.openclaw_sync_transcript_limit,
        client=client,
    )


def wait_openclaw_bridge_events(after_cursor: int, session_key: str | None = None, timeout_seconds: int = 30) -> dict[str, Any] | None:
    if not settings.openclaw_bridge_enabled:
        return None
    bridge_url = settings.openclaw_bridge_url.rstrip('/')
    try:
        payload = {'afterCursor': after_cursor, 'timeoutMs': timeout_seconds * 1000}
        if session_key:
            payload['sessionKey'] = session_key
        req = urllib.request.Request(
            f'{bridge_url}/wait-events',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds + 5) as resp:
            parsed = json.loads(resp.read().decode('utf-8'))
            if parsed.get('ok'):
                return parsed
    except Exception as exc:
        LOGGER.warning('openclaw_bridge_event_read_failed', extra={'event_payload': {'action': 'events_wait', 'error': str(exc)}})
    return None

def poll_openclaw_bridge_events(after_cursor: int, session_key: str | None = None) -> dict[str, Any] | None:
    if not settings.openclaw_bridge_enabled:
        return None
    bridge_url = settings.openclaw_bridge_url.rstrip('/')
    try:
        payload = {'afterCursor': after_cursor}
        if session_key:
            payload['sessionKey'] = session_key
        req = urllib.request.Request(
            f'{bridge_url}/poll-events',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=settings.openclaw_bridge_timeout_seconds) as resp:
            parsed = json.loads(resp.read().decode('utf-8'))
            if parsed.get('ok'):
                return parsed
    except Exception as exc:
        LOGGER.warning('openclaw_bridge_event_read_failed', extra={'event_payload': {'action': 'events_poll', 'error': str(exc)}})
    return None

def consume_openclaw_events_once(db: Session, *, source: str = "default", timeout_seconds: int | None = None) -> int:
    timeout_seconds = timeout_seconds or settings.openclaw_sync_poll_timeout_seconds
    cursor_row = db.query(OpenClawSyncCursor).filter(OpenClawSyncCursor.source == source).first()
    cursor_str = cursor_row.cursor_value if cursor_row else None
    
    try:
        after_cursor = int(cursor_str) if cursor_str is not None else 0
    except ValueError:
        after_cursor = 0

    processed = 0
    bridge_success = False
    payload = None

    if settings.openclaw_bridge_enabled:
        # First wait for event
        wait_res = wait_openclaw_bridge_events(after_cursor=after_cursor, timeout_seconds=timeout_seconds)
        if wait_res is not None:
            bridge_success = True
            if wait_res.get('event'):
                # Event arrived, poll the rest to grab a batch
                poll_res = poll_openclaw_bridge_events(after_cursor=after_cursor)
                if poll_res is not None:
                    payload = poll_res
                    LOGGER.info('openclaw_bridge_event_read_success', extra={'event_payload': {'action': 'events_poll', 'events_count': len(payload.get('events', []))}})
                else:
                    # Fallback to just the single event
                    payload = {'events': [wait_res['event']], 'nextCursor': wait_res['event'].get('cursor', after_cursor)}
                    LOGGER.info('openclaw_bridge_event_read_success', extra={'event_payload': {'action': 'events_wait', 'events_count': 1}})
            else:
                # Timeout, no new events
                payload = {'events': [], 'nextCursor': after_cursor}
        else:
            LOGGER.warning('openclaw_bridge_event_fallback', extra={'event_payload': {'reason': 'bridge_failed_or_missing_data'}})

    # Fallback to MCP
    if not bridge_success:
        LOGGER.info('openclaw_mcp_event_invoked', extra={'event_payload': {'after_cursor': after_cursor}})
        with OpenClawMCPClient() as client:
            try:
                # pass after_cursor instead of just cursor for better compatibility
                payload = client.events_wait(cursor=after_cursor, timeout_seconds=timeout_seconds)
                if not isinstance(payload, dict):
                    payload = client.events_poll(cursor=after_cursor)
            except OpenClawMCPError as e:
                LOGGER.warning('openclaw_mcp_event_failed', extra={'event_payload': {'error': str(e)}})
                payload = client.events_poll(cursor=after_cursor)

    if not isinstance(payload, dict):
        return 0

    # Normal MCP returns {"event": ...} or {"events": ...} or nextCursor or cursor
    next_cursor = payload.get("cursor") or payload.get("nextCursor")
    events = payload.get("events") or payload.get("items") or []
    
    # Handle MCP's events_wait single-event return structure: {"event": {...}}
    if not events and payload.get("event"):
        events = [payload.get("event")]
        if next_cursor is None:
            next_cursor = events[0].get("cursor")

    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or event.get("eventType") or "message") not in {"message", "inbound_message"}:
            continue
        session_key = _extract_event_session_key(event)
        if not session_key:
            continue
        link = db.query(OpenClawConversationLink).filter(OpenClawConversationLink.session_key == session_key).first()
        if link is None:
            try:
                payload_conv = None
                if settings.openclaw_bridge_enabled:
                    payload_conv, _ = read_openclaw_bridge_conversation(session_key, limit=1)
                if payload_conv is None:
                    # In fallback we need to open client again, or use a new one
                    with OpenClawMCPClient() as mcp_client:
                        payload_conv = mcp_client.conversation_get(session_key)
                route = _extract_route(payload_conv) if isinstance(payload_conv, dict) else None
                if route and route.get("recipient"):
                    from ..models import Ticket
                    from ..enums import TicketStatus
                    contact_id = route.get("recipient")
                    ticket = db.query(Ticket).filter(
                        (Ticket.source_chat_id == contact_id) | (Ticket.preferred_reply_contact == contact_id),
                        Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled])
                    ).order_by(Ticket.updated_at.desc()).first()
                    if ticket:
                        link = ensure_openclaw_conversation_link(db, ticket=ticket, session_key=session_key, route=route)
                        db.flush()
            except Exception as e:
                import logging
                logging.getLogger("nexusdesk").warning(f"Auto-link failed for {session_key}: {e}")
        if link is None:
            continue
            
        sync_openclaw_conversation(
            db,
            ticket_id=link.ticket_id,
            session_key=session_key,
            limit=settings.openclaw_sync_transcript_limit,
            client=None, # will fallback/bridge inside
        )
        processed += 1

    if next_cursor is not None:
        upsert_openclaw_sync_cursor(db, source=source, cursor_value=str(next_cursor))
        db.flush()
        
    return processed

