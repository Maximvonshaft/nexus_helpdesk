from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import Customer, Ticket
from ..utils.time import utc_now
from ..webchat_models import WebChatAuditLog, WebChatHandoffRequest, WebChatSession, WebChatSite, WebChatTicketUpgradeLink
from .openclaw_bridge import ensure_openclaw_conversation_link
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError


def _csv_to_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(',') if item.strip()]


def site_allowed_origins(site: WebChatSite) -> list[str]:
    return _csv_to_list(site.allowed_origins_csv)


def serialize_site(site: WebChatSite) -> dict[str, Any]:
    return {
        'id': site.id,
        'site_key': site.site_key,
        'name': site.name,
        'widget_title': site.widget_title,
        'logo_url': site.logo_url,
        'welcome_message': site.welcome_message,
        'default_language': site.default_language,
        'allowed_origins': site_allowed_origins(site),
        'theme_json': site.theme_json,
        'business_hours_json': site.business_hours_json,
        'mapped_market_id': site.mapped_market_id,
        'mapped_team_id': site.mapped_team_id,
        'mapped_openclaw_agent': site.mapped_openclaw_agent,
        'is_active': site.is_active,
        'created_at': site.created_at,
        'updated_at': site.updated_at,
    }


def hash_ip(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def log_webchat_audit(
    db: Session,
    *,
    event_type: str,
    status_value: str = 'ok',
    site_id: int | None = None,
    session_id: int | None = None,
    ticket_id: int | None = None,
    user_id: int | None = None,
    request_id: str | None = None,
    origin: str | None = None,
    ip_value: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    row = WebChatAuditLog(
        site_id=site_id,
        session_id=session_id,
        ticket_id=ticket_id,
        user_id=user_id,
        request_id=request_id,
        event_type=event_type,
        status=status_value,
        origin=origin,
        ip_hash=hash_ip(ip_value),
        payload_json=payload,
    )
    db.add(row)


def build_session_key(site_key: str, browser_session_id: str) -> str:
    return f'webchat:{site_key}:{browser_session_id}'


def ensure_site(db: Session, site_key: str) -> WebChatSite:
    site = db.query(WebChatSite).filter(WebChatSite.site_key == site_key, WebChatSite.is_active.is_(True)).first()
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='WebChat site not found')
    return site


def assert_origin_allowed(site: WebChatSite, origin: str | None) -> None:
    allowed = site_allowed_origins(site)
    if not allowed:
        return
    if not origin or origin not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Origin not allowed for this site')


def ensure_session(
    db: Session,
    *,
    site: WebChatSite,
    visitor_id: str | None,
    browser_session_id: str | None,
    locale: str | None,
    timezone: str | None,
    origin: str | None,
) -> WebChatSession:
    resolved_visitor = visitor_id or f'vst_{secrets.token_hex(12)}'
    resolved_browser_session = browser_session_id or f'bsn_{secrets.token_hex(12)}'
    row = db.query(WebChatSession).filter(WebChatSession.browser_session_id == resolved_browser_session).first()
    if row is None:
        row = WebChatSession(
            site_id=site.id,
            visitor_id=resolved_visitor,
            browser_session_id=resolved_browser_session,
            openclaw_session_key=build_session_key(site.site_key, resolved_browser_session),
            origin=origin,
            locale=locale,
            timezone=timezone,
            status='active',
            handoff_status='none',
            expires_at=utc_now() + timedelta(hours=24),
        )
        db.add(row)
        db.flush()
    else:
        row.last_active_at = utc_now()
        row.updated_at = utc_now()
        row.locale = locale or row.locale
        row.timezone = timezone or row.timezone
        row.origin = origin or row.origin
        row.expires_at = utc_now() + timedelta(hours=24)
    return row


def serialize_session(row: WebChatSession) -> dict[str, Any]:
    return {
        'id': row.id,
        'site_id': row.site_id,
        'ticket_id': row.ticket_id,
        'visitor_id': row.visitor_id,
        'browser_session_id': row.browser_session_id,
        'status': row.status,
        'handoff_status': row.handoff_status,
        'origin': row.origin,
        'locale': row.locale,
        'timezone': row.timezone,
        'last_message_preview': row.last_message_preview,
        'last_message_at': row.last_message_at,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
        'last_active_at': row.last_active_at,
        'expires_at': row.expires_at,
    }


def normalize_messages(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message_id = item.get('message_id') or item.get('messageId') or item.get('id')
        role = item.get('role') or item.get('senderRole') or item.get('authorRole') or 'assistant'
        text = item.get('text') or item.get('body') or item.get('message')
        if not text and isinstance(item.get('content'), list):
            parts: list[str] = []
            for block in item['content']:
                if isinstance(block, dict):
                    value = block.get('text') or block.get('content')
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
            text = '\n'.join(parts) if parts else None
        normalized.append({
            'id': str(message_id or secrets.token_hex(6)),
            'role': str(role),
            'author_name': item.get('author_name') or item.get('author') or item.get('sender'),
            'text': text,
            'created_at': item.get('received_at') or item.get('created_at'),
        })
    return normalized


def read_session_history(db: Session, session: WebChatSession, *, limit: int = 50) -> list[dict[str, Any]]:
    try:
        with OpenClawMCPClient() as client:
            messages = client.messages_read(session.openclaw_session_key, limit=limit)
    except OpenClawMCPError:
        return []
    normalized = normalize_messages(messages)
    if normalized:
        latest = normalized[-1]
        session.last_message_preview = latest.get('text')
        session.last_message_at = utc_now()
    return normalized


def send_session_message(db: Session, session: WebChatSession, text: str) -> None:
    with OpenClawMCPClient() as client:
        client.messages_send(session.openclaw_session_key, text)
    session.last_message_preview = text[:500]
    session.last_message_at = utc_now()
    session.last_active_at = utc_now()
    session.updated_at = utc_now()


def create_handoff_request(db: Session, session: WebChatSession, *, reason: str | None, note: str | None, requested_by: str) -> WebChatHandoffRequest:
    row = WebChatHandoffRequest(
        session_id=session.id,
        requested_by=requested_by,
        status='queued',
        reason=reason,
        note=note,
    )
    db.add(row)
    session.handoff_status = 'requested'
    session.updated_at = utc_now()
    db.flush()
    return row


def _ensure_customer(db: Session, *, name: str | None, email: str | None, visitor_id: str) -> Customer | None:
    if not name and not email:
        return None
    existing = None
    if email:
        existing = db.query(Customer).filter(Customer.email_normalized == email.strip().lower()).first()
    if existing:
        return existing
    customer = Customer(
        name=name or visitor_id,
        email=email,
        email_normalized=email.strip().lower() if email else None,
        external_ref=visitor_id,
    )
    db.add(customer)
    db.flush()
    return customer


def upgrade_session_to_ticket(
    db: Session,
    session: WebChatSession,
    *,
    title: str | None,
    description: str | None,
    customer_name: str | None,
    customer_email: str | None,
    created_by_user_id: int | None,
) -> tuple[Ticket, WebChatTicketUpgradeLink]:
    customer = _ensure_customer(db, name=customer_name, email=customer_email, visitor_id=session.visitor_id)
    history = read_session_history(db, session, limit=20)
    last_user_message = next((item.get('text') for item in reversed(history) if item.get('role') == 'user' and item.get('text')), None)
    ticket = Ticket(
        ticket_no=f'W{int(utc_now().timestamp())}{session.id}',
        title=title or session.last_message_preview or 'Website chat request',
        description=description or last_user_message or 'Created from website chat session',
        customer_id=customer.id if customer else None,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        team_id=session.site.mapped_team_id,
        market_id=session.site.mapped_market_id,
        created_by=created_by_user_id,
        conversation_state=ConversationState.human_review_required,
        source_chat_id=session.browser_session_id,
        customer_request=last_user_message,
        last_customer_message=last_user_message,
        preferred_reply_channel='web_chat',
        preferred_reply_contact=session.visitor_id,
    )
    db.add(ticket)
    db.flush()
    ensure_openclaw_conversation_link(db, ticket=ticket, session_key=session.openclaw_session_key, channel='web_chat', recipient=session.visitor_id)
    session.ticket_id = ticket.id
    session.handoff_status = 'ticket_created'
    link = WebChatTicketUpgradeLink(
        session_id=session.id,
        ticket_id=ticket.id,
        upgrade_type='session_to_ticket',
        created_by_user_id=created_by_user_id,
    )
    db.add(link)
    db.flush()
    return ticket, link
