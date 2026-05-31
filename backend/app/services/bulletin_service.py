from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import MarketBulletin, Ticket
from ..enums import SourceChannel, TicketStatus
from ..utils.time import utc_now


TERMINAL_TICKET_STATUSES = {TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}
CHANNEL_ALIASES = {
    "chat": "web_chat",
    "webchat": "web_chat",
    "web_chat": "web_chat",
    "mail": "email",
    "e-mail": "email",
}


def normalize_bulletin_country_code(value: str | None) -> str | None:
    cleaned = (value or "").strip().upper()
    return cleaned or None


def normalize_bulletin_channels(value: str | None) -> list[str]:
    channels: list[str] = []
    for raw in (value or "").split(","):
        cleaned = raw.strip().lower().replace(" ", "_")
        if not cleaned:
            continue
        channels.append(CHANNEL_ALIASES.get(cleaned, cleaned))
    return sorted(set(channels))


def bulletin_audit_snapshot(row: MarketBulletin) -> dict[str, Any]:
    return {
        "id": row.id,
        "market_id": row.market_id,
        "country_code": row.country_code,
        "title": row.title,
        "summary": row.summary,
        "category": row.category,
        "channels": normalize_bulletin_channels(row.channels_csv),
        "audience": row.audience,
        "severity": row.severity,
        "auto_inject_to_ai": row.auto_inject_to_ai,
        "is_active": row.is_active,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
    }


def build_bulletin_impact_preview(
    db: Session,
    *,
    market_id: int | None,
    country_code: str | None,
    channels_csv: str | None,
    audience: str | None,
    auto_inject_to_ai: bool,
    is_active: bool,
    starts_at=None,
    ends_at=None,
    limit: int = 5,
) -> dict[str, Any]:
    now = utc_now()
    normalized_country = normalize_bulletin_country_code(country_code)
    channels = normalize_bulletin_channels(channels_csv)
    query = db.query(Ticket).filter(~Ticket.status.in_(TERMINAL_TICKET_STATUSES))

    if market_id is not None and normalized_country:
        query = query.filter(or_(Ticket.market_id == market_id, Ticket.country_code == normalized_country))
    elif market_id is not None:
        query = query.filter(Ticket.market_id == market_id)
    elif normalized_country:
        query = query.filter(Ticket.country_code == normalized_country)

    rows = query.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).limit(500).all()
    if channels:
        rows = [row for row in rows if _ticket_channel(row) in channels]

    channel_counts = Counter(_ticket_channel(row) for row in rows)
    ready_to_reply = sum(1 for row in rows if row.conversation_state.value in {"ready_to_reply", "human_review_required", "human_owned"})
    window_status = "active"
    if not is_active:
        window_status = "inactive"
    elif starts_at and starts_at > now:
        window_status = "scheduled"
    elif ends_at and ends_at < now:
        window_status = "expired"

    scope_parts = []
    if market_id is not None:
        scope_parts.append(f"market:{market_id}")
    if normalized_country:
        scope_parts.append(f"country:{normalized_country}")
    if channels:
        scope_parts.append(f"channels:{','.join(channels)}")
    scope_label = " · ".join(scope_parts) if scope_parts else "global"

    return {
        "matching_tickets": len(rows),
        "ready_to_reply_tickets": ready_to_reply,
        "channel_counts": [
            {"channel": channel, "count": count}
            for channel, count in sorted(channel_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "sample_tickets": [
            {
                "id": row.id,
                "ticket_no": row.ticket_no,
                "title": row.title,
                "status": row.status.value,
                "channel": _ticket_channel(row),
                "updated_at": row.updated_at,
            }
            for row in rows[: max(1, min(limit, 20))]
        ],
        "window_status": window_status,
        "scope_label": scope_label,
        "auto_inject_to_ai": auto_inject_to_ai,
        "ai_context_enabled": bool(auto_inject_to_ai and is_active and (audience or "customer") in {"customer", "both", "all"}),
    }


def _ticket_channel(ticket: Ticket) -> str:
    preferred = (ticket.preferred_reply_channel or "").strip().lower().replace(" ", "_")
    if preferred:
        return CHANNEL_ALIASES.get(preferred, preferred)
    source = ticket.source_channel.value if isinstance(ticket.source_channel, SourceChannel) else str(ticket.source_channel or "")
    return CHANNEL_ALIASES.get(source, source)


def list_active_bulletins(db: Session, *, market_id: int | None, country_code: str | None, channel: str | None = None) -> list[MarketBulletin]:
    now = utc_now()
    query = db.query(MarketBulletin).filter(MarketBulletin.is_active.is_(True))
    query = query.filter(or_(MarketBulletin.starts_at.is_(None), MarketBulletin.starts_at <= now))
    query = query.filter(or_(MarketBulletin.ends_at.is_(None), MarketBulletin.ends_at >= now))
    if market_id is not None or country_code:
        country_code = country_code.upper() if country_code else None
        query = query.filter(
            or_(
                MarketBulletin.market_id == market_id if market_id is not None else False,
                MarketBulletin.country_code == country_code if country_code else False,
                (MarketBulletin.market_id.is_(None) & (MarketBulletin.country_code.is_(None)))
            )
        )
    if channel:
        like = f'%{CHANNEL_ALIASES.get(channel.lower(), channel.lower())}%'
        query = query.filter(or_(MarketBulletin.channels_csv.is_(None), MarketBulletin.channels_csv == '', MarketBulletin.channels_csv.ilike(like)))
    return query.order_by(MarketBulletin.severity.desc(), MarketBulletin.created_at.desc()).all()


def build_bulletin_context(db: Session, *, ticket: Ticket) -> str:
    rows = list_active_bulletins(
        db,
        market_id=ticket.market_id,
        country_code=ticket.country_code,
        channel=ticket.preferred_reply_channel or (ticket.source_channel.value if ticket.source_channel else None),
    )
    rows = [r for r in rows if r.auto_inject_to_ai and r.audience in {'customer', 'both', 'all'}]
    if not rows:
        return ''
    parts = []
    for row in rows[:5]:
        headline = row.title.strip()
        summary = (row.summary or row.body).strip()
        parts.append(f'- {headline}: {summary}')
    return '\n'.join(parts)
