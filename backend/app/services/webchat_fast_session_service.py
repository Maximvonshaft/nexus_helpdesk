from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ..models import ChannelAccount, Customer, Market, Ticket
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .ticket_service import generate_ticket_no

FAST_ORIGIN = "webchat-fast"
FAST_CONTEXT_LIMIT = 10
ACTIVE_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
}
TRACKING_RE = re.compile(r"\b[A-Z0-9][A-Z0-9._-]{7,47}\b", re.IGNORECASE)


@dataclass(frozen=True)
class FastBusinessState:
    intent: str
    issue_type: str
    tracking_number: str | None
    fast_issue_key: str
    missing_fields: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "issue_type": self.issue_type,
            "tracking_number": self.tracking_number,
            "fast_issue_key": self.fast_issue_key,
            "missing_fields": list(self.missing_fields),
        }


@dataclass(frozen=True)
class FastRoutingContext:
    """Resolved market/channel-account context for a Fast Lane handoff ticket."""

    market_id: int | None = None
    country_code: str | None = None
    channel_account_id: int | None = None


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    return cleaned[:limit] if cleaned else None


def _upper(value: Any, limit: int) -> str | None:
    cleaned = _clip(value, limit)
    return cleaned.upper() if cleaned else None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _safe_public_id(*, tenant_key: str, channel_key: str, session_id: str) -> str:
    return f"wcf_{_sha(f'fast:{tenant_key}:{channel_key}:{session_id}')[:24]}"


def _visitor_payload(visitor: Any) -> dict[str, Any]:
    if visitor is None:
        return {}
    if hasattr(visitor, "model_dump"):
        return visitor.model_dump(exclude_none=True)
    if isinstance(visitor, dict):
        return {k: v for k, v in visitor.items() if v is not None}
    return {}


def _request_meta(request: Any) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    return _clip(request.headers.get("referer"), 700), _clip(request.headers.get("user-agent"), 300)


def clean_fast_context(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "visitor").lower()
        role = "agent" if role in {"ai", "assistant", "agent", "bot"} else "visitor"
        text = str(item.get("text") or item.get("body") or item.get("content") or "").strip()
        if text:
            out.append({"role": role, "text": text[:500]})
    return out[-FAST_CONTEXT_LIMIT:]


def merge_fast_context(server_context: list[dict[str, Any]] | None, frontend_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in clean_fast_context(server_context) + clean_fast_context(frontend_context):
        key = (item["role"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-FAST_CONTEXT_LIMIT:]


def resolve_fast_routing_context(
    db: Session,
    *,
    country_code: str | None = None,
    market_code: str | None = None,
    channel_account_key: str | None = None,
) -> FastRoutingContext:
    """Resolve public Fast Lane routing hints into internal ticket routing ids.

    Public WebChat payloads should not need to know database ids. This resolver
    accepts stable business keys and falls back conservatively:
    explicit channel account -> matching market -> global OpenClaw account.
    """

    normalized_country = _upper(country_code, 8)
    normalized_market = _upper(market_code, 16)
    normalized_account = _clip(channel_account_key, 160)

    market: Market | None = None
    if normalized_market:
        market = db.execute(
            select(Market).where(Market.code == normalized_market, Market.is_active.is_(True)).limit(1)
        ).scalar_one_or_none()
    if market is None and normalized_country:
        market = db.execute(
            select(Market)
            .where(Market.country_code == normalized_country, Market.is_active.is_(True))
            .order_by(Market.id.asc())
            .limit(1)
        ).scalar_one_or_none()

    account: ChannelAccount | None = None
    if normalized_account:
        account = db.execute(
            select(ChannelAccount)
            .where(ChannelAccount.account_id == normalized_account, ChannelAccount.is_active.is_(True))
            .limit(1)
        ).scalar_one_or_none()

    if account is None and market is not None:
        account = db.execute(
            select(ChannelAccount)
            .where(
                ChannelAccount.provider == "openclaw",
                ChannelAccount.market_id == market.id,
                ChannelAccount.is_active.is_(True),
            )
            .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
            .limit(1)
        ).scalar_one_or_none()

    if account is None:
        account = db.execute(
            select(ChannelAccount)
            .where(
                ChannelAccount.provider == "openclaw",
                ChannelAccount.market_id.is_(None),
                ChannelAccount.is_active.is_(True),
            )
            .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
            .limit(1)
        ).scalar_one_or_none()

    if market is None and account is not None and account.market_id is not None:
        market = db.get(Market, account.market_id)

    resolved_country = normalized_country or (market.country_code if market is not None else None)
    return FastRoutingContext(
        market_id=market.id if market is not None else None,
        country_code=resolved_country,
        channel_account_id=account.id if account is not None else None,
    )


def extract_tracking_number(*, body: str, context: list[dict[str, Any]] | None = None) -> str | None:
    haystack = "\n".join([body or ""] + [str(item.get("text") or "") for item in (context or []) if isinstance(item, dict)]).upper()
    for match in TRACKING_RE.finditer(haystack):
        token = re.sub(r"[^A-Z0-9]", "", match.group(0))
        if 8 <= len(token) <= 40 and any(ch.isdigit() for ch in token):
            return token
    return None


def extract_fast_business_state(*, body: str, context: list[dict[str, Any]] | None, session_id: str) -> FastBusinessState:
    text = " ".join([body or ""] + [str(item.get("text") or "") for item in (context or []) if isinstance(item, dict)]).lower()
    tracking = extract_tracking_number(body=body, context=context)
    if any(w in text for w in ("lost", "missing", "not received", "damaged", "stolen", "verloren", "nicht erhalten", "丢", "没收到", "未收到", "破损", "损坏")):
        issue_type = "lost_or_damaged_parcel"
        intent = "lost_or_damaged_parcel" if tracking else "tracking_missing_number"
    elif any(w in text for w in ("redelivery", "reschedule", "deliver again", "重新派送", "改派")):
        issue_type = "delivery_reschedule"
        intent = "delivery_reschedule"
    elif any(w in text for w in ("address", "adresse", "地址")):
        issue_type = "address_issue"
        intent = "address_issue"
    elif tracking:
        issue_type = "tracking_lookup"
        intent = "tracking_lookup"
    elif any(w in text for w in ("track", "tracking", "where is", "parcel", "package", "shipment", "paket", "包裹", "运单")):
        issue_type = "tracking_lookup"
        intent = "tracking_missing_number"
    else:
        issue_type = "general_question"
        intent = "general_question"
    fast_issue_key = f"tracking:{tracking}:intent:{issue_type}" if tracking else f"session:{session_id}:intent:{issue_type}"
    missing_fields = ("tracking_number",) if not tracking and issue_type in {"tracking_lookup", "lost_or_damaged_parcel", "delivery_reschedule"} else ()
    return FastBusinessState(intent=intent, issue_type=issue_type, tracking_number=tracking, fast_issue_key=fast_issue_key[:240], missing_fields=missing_fields)


def get_or_create_fast_conversation(db: Session, *, tenant_key: str, channel_key: str, session_id: str, request: Any = None, visitor: Any = None) -> WebchatConversation:
    tenant = _clip(tenant_key, 120) or "default"
    channel = _clip(channel_key, 120) or "website"
    session = _clip(session_id, 120) or "unknown"
    row = db.execute(
        select(WebchatConversation).where(
            WebchatConversation.tenant_key == tenant,
            WebchatConversation.channel_key == channel,
            WebchatConversation.fast_session_id == session,
            WebchatConversation.origin == FAST_ORIGIN,
            WebchatConversation.status == "open",
        ).limit(1)
    ).scalar_one_or_none()
    now = utc_now()
    if row is not None:
        row.last_seen_at = now
        row.updated_at = now
        db.flush()
        return row
    visitor_data = _visitor_payload(visitor)
    page_url, user_agent = _request_meta(request)
    row = WebchatConversation(
        public_id=_safe_public_id(tenant_key=tenant, channel_key=channel, session_id=session),
        visitor_token_hash=_sha(f"fast-visitor:{tenant}:{channel}:{session}"),
        tenant_key=tenant,
        channel_key=channel,
        ticket_id=None,
        visitor_name=_clip(visitor_data.get("name"), 160),
        visitor_email=_clip(visitor_data.get("email"), 200),
        visitor_phone=_clip(visitor_data.get("phone"), 80),
        visitor_ref=session,
        origin=FAST_ORIGIN,
        page_url=page_url,
        user_agent=user_agent,
        status="open",
        fast_session_id=session,
        created_at=now,
        updated_at=now,
        last_seen_at=now,
        fast_context_updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _find_message(db: Session, *, conversation_id: int, client_message_id: str) -> WebchatMessage | None:
    return db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversation_id, WebchatMessage.client_message_id == client_message_id).limit(1)).scalar_one_or_none()


def _append_message(db: Session, *, conversation: WebchatConversation, direction: str, body: str, client_message_id: str, author_label: str, metadata: dict[str, Any] | None = None) -> WebchatMessage:
    msg_id = _clip(client_message_id, 120) or f"fast-{direction}-{_sha(body or '')[:12]}"
    existing = _find_message(db, conversation_id=conversation.id, client_message_id=msg_id)
    if existing is not None:
        if conversation.ticket_id and existing.ticket_id is None:
            existing.ticket_id = conversation.ticket_id
        return existing
    now = utc_now()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        direction=direction,
        body=body,
        body_text=body,
        message_type="text",
        metadata_json=_json(metadata or {}),
        client_message_id=msg_id,
        delivery_status="sent",
        author_label=author_label,
        created_at=now,
    )
    db.add(message)
    conversation.fast_last_client_message_id = msg_id
    conversation.fast_context_updated_at = now
    conversation.updated_at = now
    db.flush()
    return message


def append_fast_visitor_message(db: Session, *, conversation: WebchatConversation, body: str, client_message_id: str, metadata: dict[str, Any] | None = None) -> WebchatMessage:
    return _append_message(db, conversation=conversation, direction="visitor", body=body, client_message_id=client_message_id, author_label="Customer", metadata=metadata)


def append_fast_ai_message(db: Session, *, conversation: WebchatConversation, reply: str | None, client_message_id: str, metadata: dict[str, Any] | None = None) -> WebchatMessage | None:
    cleaned = _clip(reply, 2000)
    if not cleaned:
        return None
    return _append_message(db, conversation=conversation, direction="ai", body=cleaned, client_message_id=f"{client_message_id}:ai"[:120], author_label="Speedy", metadata=metadata)


def append_fast_system_handoff_message(db: Session, *, conversation: WebchatConversation, handoff_reason: str | None, recommended_agent_action: str | None, client_message_id: str) -> WebchatMessage:
    return _append_message(
        db,
        conversation=conversation,
        direction="system",
        body="WebChat Fast Lane created a human-review handoff ticket.",
        client_message_id=f"{client_message_id}:handoff"[:120],
        author_label="System",
        metadata={"handoff_reason": handoff_reason, "recommended_agent_action": recommended_agent_action, "source": "webchat_fast_handoff"},
    )


def build_fast_server_context(db: Session, *, conversation: WebchatConversation, limit: int = FAST_CONTEXT_LIMIT, exclude_message_id: int | None = None) -> list[dict[str, str]]:
    rows = db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversation.id).order_by(WebchatMessage.id.desc()).limit(max(limit * 2, limit + 2))).scalars().all()
    items = []
    for row in reversed(rows):
        if exclude_message_id is not None and row.id == exclude_message_id:
            continue
        if row.direction in {"visitor", "ai", "agent"}:
            items.append({"role": row.direction, "text": row.body_text or row.body})
    return clean_fast_context(items)


def update_fast_business_state(db: Session, *, conversation: WebchatConversation, business_state: FastBusinessState, client_message_id: str) -> None:
    conversation.fast_issue_key = business_state.fast_issue_key
    conversation.last_intent = business_state.intent
    if business_state.tracking_number:
        conversation.last_tracking_number = business_state.tracking_number
    conversation.fast_last_client_message_id = _clip(client_message_id, 120)
    conversation.fast_context_updated_at = utc_now()
    conversation.updated_at = utc_now()
    db.flush()


def _find_or_create_customer(db: Session, *, conversation: WebchatConversation) -> Customer:
    ext_ref = conversation.visitor_ref or conversation.public_id
    existing = db.execute(select(Customer).where(Customer.external_ref == ext_ref).limit(1)).scalar_one_or_none()
    if existing is not None:
        return existing
    customer = Customer(name=conversation.visitor_name or conversation.visitor_email or conversation.visitor_phone or f"Webchat Visitor {conversation.public_id[-6:]}", email=conversation.visitor_email, email_normalized=conversation.visitor_email.lower() if conversation.visitor_email else None, phone=conversation.visitor_phone, phone_normalized=conversation.visitor_phone.lower() if conversation.visitor_phone else None, external_ref=ext_ref)
    db.add(customer)
    db.flush()
    return customer


def _apply_routing_context(ticket: Ticket, routing_context: FastRoutingContext | None) -> None:
    if routing_context is None:
        return
    if routing_context.market_id is not None and ticket.market_id is None:
        ticket.market_id = routing_context.market_id
    if routing_context.country_code and not ticket.country_code:
        ticket.country_code = routing_context.country_code
    if routing_context.channel_account_id is not None and ticket.channel_account_id is None:
        ticket.channel_account_id = routing_context.channel_account_id


def _find_active_ticket(db: Session, *, conversation: WebchatConversation, business_state: FastBusinessState) -> Ticket | None:
    if conversation.ticket_id is not None:
        ticket = db.get(Ticket, conversation.ticket_id)
        if ticket is not None and ticket.status in ACTIVE_TICKET_STATUSES:
            return ticket
    dedupe_key = f"webchat-fast-issue:{conversation.tenant_key}:{conversation.channel_key}:{business_state.fast_issue_key}"[:300]
    ticket = db.execute(select(Ticket).where(Ticket.source_dedupe_key == dedupe_key, Ticket.status.in_(ACTIVE_TICKET_STATUSES)).limit(1)).scalar_one_or_none()
    if ticket is not None:
        return ticket
    if business_state.tracking_number:
        return db.execute(select(Ticket).where(Ticket.tracking_number == business_state.tracking_number, Ticket.case_type == business_state.issue_type, Ticket.source_channel == SourceChannel.web_chat, Ticket.status.in_(ACTIVE_TICKET_STATUSES)).limit(1)).scalar_one_or_none()
    return None


def get_or_create_fast_ticket(
    db: Session,
    *,
    conversation: WebchatConversation,
    business_state: FastBusinessState,
    handoff_reason: str | None,
    recommended_agent_action: str | None,
    customer_message: str | None = None,
    routing_context: FastRoutingContext | None = None,
) -> Ticket:
    existing = _find_active_ticket(db, conversation=conversation, business_state=business_state)
    if existing is not None:
        conversation.ticket_id = existing.id
        _apply_routing_context(existing, routing_context)
        db.execute(WebchatMessage.__table__.update().where(WebchatMessage.conversation_id == conversation.id, WebchatMessage.ticket_id.is_(None)).values(ticket_id=existing.id))
        db.flush()
        return existing
    customer = _find_or_create_customer(db, conversation=conversation)
    dedupe_key = f"webchat-fast-issue:{conversation.tenant_key}:{conversation.channel_key}:{business_state.fast_issue_key}"[:300]
    ticket = Ticket(
        ticket_no=generate_ticket_no(),
        title=f"WebChat handoff · {business_state.tracking_number or business_state.issue_type}"[:255],
        description=("AI handoff snapshot\n\n" f"Customer message: {customer_message or ''}\n\n" f"Reason: {handoff_reason or 'ai_requested_handoff'}")[:4000],
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number=business_state.tracking_number,
        source_chat_id=f"webchat-fast:{conversation.public_id}"[:120],
        source_dedupe_key=dedupe_key,
        case_type=business_state.issue_type,
        customer_request=customer_message,
        last_customer_message=customer_message,
        required_action=recommended_agent_action,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=conversation.public_id,
        market_id=routing_context.market_id if routing_context else None,
        country_code=routing_context.country_code if routing_context else None,
        channel_account_id=routing_context.channel_account_id if routing_context else None,
    )
    db.add(ticket)
    db.flush()
    conversation.ticket_id = ticket.id
    conversation.fast_issue_key = business_state.fast_issue_key
    conversation.last_intent = business_state.intent
    if business_state.tracking_number:
        conversation.last_tracking_number = business_state.tracking_number
    db.execute(WebchatMessage.__table__.update().where(WebchatMessage.conversation_id == conversation.id, WebchatMessage.ticket_id.is_(None)).values(ticket_id=ticket.id))
    db.flush()
    return ticket
