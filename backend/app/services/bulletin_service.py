from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import MarketBulletin, Ticket
from ..utils.time import utc_now


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
        like = f'%{channel.lower()}%'
        query = query.filter(or_(MarketBulletin.channels_csv.is_(None), MarketBulletin.channels_csv == '', MarketBulletin.channels_csv.ilike(like)))
    return query.order_by(MarketBulletin.severity.desc(), MarketBulletin.created_at.desc()).all()


def build_bulletin_context(db: Session, *, ticket: Ticket) -> str:
    rows = list_active_bulletins(
        db,
        market_id=ticket.market_id,
        country_code=ticket.country_code,
        channel=ticket.preferred_reply_channel or (ticket.source_channel.value if ticket.source_channel else None),
    )
    rows = [r for r in rows if r.auto_inject_to_ai and r.audience in {'customer', 'all'}]
    if not rows:
        return ''
    parts = []
    for row in rows[:5]:
        headline = row.title.strip()
        summary = (row.summary or row.body).strip()
        parts.append(f'- {headline}: {summary}')
    return '\n'.join(parts)
