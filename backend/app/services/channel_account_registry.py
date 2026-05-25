from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import ChannelAccount


EMAIL_PROVIDER = "email"
OPENCLAW_PROVIDER_CHANNELS = frozenset({"whatsapp", "telegram", "sms"})


def resolve_channel_account_for_provider(
    db: Session,
    *,
    provider: str,
    market_id: int | None = None,
    account_id: str | None = None,
) -> ChannelAccount | None:
    normalized_provider = (provider or "").strip().lower()
    if not normalized_provider:
        return None
    query = db.query(ChannelAccount).filter(
        ChannelAccount.provider == normalized_provider,
        ChannelAccount.is_active.is_(True),
    )
    if account_id:
        return query.filter(ChannelAccount.account_id == account_id).first()
    if market_id is not None:
        row = query.filter(ChannelAccount.market_id == market_id).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
        if row is not None:
            return row
    return query.filter(ChannelAccount.market_id.is_(None)).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
