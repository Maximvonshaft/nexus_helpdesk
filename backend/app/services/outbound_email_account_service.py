from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Market, OutboundEmailAccount, Ticket

EMAIL_SECURITY_MODES = frozenset({"starttls", "ssl", "plain"})


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_host(value: str) -> str:
    return value.strip().lower()


def normalize_email(value: str | None) -> str | None:
    cleaned = clean_optional_text(value)
    return cleaned.lower() if cleaned else None


def validate_active_market(db: Session, market_id: int | None) -> None:
    if market_id is None:
        return
    market = db.query(Market).filter(Market.id == market_id, Market.is_active.is_(True)).first()
    if market is None:
        raise ValueError("Market not found or inactive")


def find_duplicate_account(
    db: Session,
    *,
    host: str,
    port: int,
    username: str,
    from_address: str,
    market_id: int | None,
    exclude_id: int | None = None,
) -> OutboundEmailAccount | None:
    query = db.query(OutboundEmailAccount).filter(
        OutboundEmailAccount.host == host,
        OutboundEmailAccount.port == port,
        OutboundEmailAccount.username == username,
        OutboundEmailAccount.from_address == from_address,
    )
    if market_id is None:
        query = query.filter(OutboundEmailAccount.market_id.is_(None))
    else:
        query = query.filter(OutboundEmailAccount.market_id == market_id)
    if exclude_id is not None:
        query = query.filter(OutboundEmailAccount.id != exclude_id)
    return query.first()


def resolve_outbound_email_account(db: Session, *, market_id: int | None = None) -> OutboundEmailAccount | None:
    query = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.is_active.is_(True))
    if market_id is not None:
        row = (
            query.filter(OutboundEmailAccount.market_id == market_id)
            .order_by(OutboundEmailAccount.priority.asc(), OutboundEmailAccount.id.asc())
            .first()
        )
        if row is not None:
            return row
    return (
        query.filter(OutboundEmailAccount.market_id.is_(None))
        .order_by(OutboundEmailAccount.priority.asc(), OutboundEmailAccount.id.asc())
        .first()
    )


def has_active_outbound_email_account(db: Session | None, *, ticket: Ticket | None = None) -> bool:
    if db is None:
        return False
    market_id = getattr(ticket, "market_id", None) if ticket is not None else None
    return resolve_outbound_email_account(db, market_id=market_id) is not None


def account_audit_snapshot(row: OutboundEmailAccount) -> dict[str, Any]:
    return {
        "id": row.id,
        "display_name": row.display_name,
        "host": row.host,
        "port": row.port,
        "username": row.username,
        "password": {"redacted": True, "configured": bool(row.password_encrypted)},
        "from_address": row.from_address,
        "reply_to": row.reply_to,
        "security_mode": row.security_mode,
        "market_id": row.market_id,
        "is_active": row.is_active,
        "priority": row.priority,
        "health_status": row.health_status,
        "last_test_status": row.last_test_status,
        "last_test_error": row.last_test_error,
    }
