from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Market, Ticket
from ..models_control_plane import ChannelOnboardingTask

ALLOWED_CHANNEL_PROVIDERS = {"whatsapp", "telegram", "sms", "email", "web_chat"}
ALLOWED_ONBOARDING_STATUSES = {"pending", "running", "success", "failed", "cancelled"}


def normalize_provider(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned not in ALLOWED_CHANNEL_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported channel provider")
    return cleaned


def normalize_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def ensure_market(db: Session, market_id: int | None) -> None:
    if market_id is None:
        return
    if db.query(Market.id).filter(Market.id == market_id, Market.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=400, detail="Market not found or inactive")


def list_accounts(db: Session) -> list[ChannelAccount]:
    return db.query(ChannelAccount).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).all()


def create_account(db: Session, payload) -> ChannelAccount:
    provider = normalize_provider(payload.provider)
    account_id = payload.account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")
    ensure_market(db, payload.market_id)
    if db.query(ChannelAccount.id).filter(ChannelAccount.account_id == account_id).first():
        raise HTTPException(status_code=409, detail="Channel account already exists")
    fallback = normalize_text(payload.fallback_account_id)
    if fallback:
        if fallback == account_id:
            raise HTTPException(status_code=400, detail="Fallback cannot point to itself")
        fallback_row = db.query(ChannelAccount).filter(ChannelAccount.account_id == fallback).first()
        if fallback_row is None:
            raise HTTPException(status_code=400, detail="Fallback channel account not found")
        if fallback_row.provider != provider:
            raise HTTPException(status_code=400, detail="Fallback provider must match primary provider")
        if payload.market_id is None and fallback_row.market_id is not None:
            raise HTTPException(status_code=400, detail="Global primary account cannot fallback to market-specific account")
        if payload.market_id is not None and fallback_row.market_id not in (None, payload.market_id):
            raise HTTPException(status_code=400, detail="Fallback market must be global or match primary market")
    row = ChannelAccount(
        provider=provider,
        account_id=account_id,
        display_name=normalize_text(payload.display_name),
        market_id=payload.market_id,
        is_active=payload.is_active,
        priority=payload.priority,
        fallback_account_id=fallback,
    )
    db.add(row)
    db.flush()
    return row


def update_account(db: Session, row: ChannelAccount, payload) -> ChannelAccount:
    values = payload.model_dump(exclude_unset=True)
    target_market_id = values.get("market_id", row.market_id)
    ensure_market(db, target_market_id)
    if "provider" in values and values["provider"] is not None and normalize_provider(values["provider"]) != row.provider:
        raise HTTPException(status_code=400, detail="provider cannot be changed after creation")
    fallback = normalize_text(values.get("fallback_account_id", row.fallback_account_id))
    if fallback:
        if fallback == row.account_id:
            raise HTTPException(status_code=400, detail="Fallback cannot point to itself")
        fallback_row = db.query(ChannelAccount).filter(ChannelAccount.account_id == fallback).first()
        if fallback_row is None:
            raise HTTPException(status_code=400, detail="Fallback channel account not found")
        if fallback_row.id == row.id:
            raise HTTPException(status_code=400, detail="Fallback cannot point to itself")
        if fallback_row.provider != row.provider:
            raise HTTPException(status_code=400, detail="Fallback provider must match primary provider")
        if target_market_id is None and fallback_row.market_id is not None:
            raise HTTPException(status_code=400, detail="Global primary account cannot fallback to market-specific account")
        if target_market_id is not None and fallback_row.market_id not in (None, target_market_id):
            raise HTTPException(status_code=400, detail="Fallback market must be global or match primary market")
    for key in ("display_name", "market_id", "is_active", "priority"):
        if key in values:
            setattr(row, key, values[key] if key != "display_name" else normalize_text(values[key]))
    if "fallback_account_id" in values:
        row.fallback_account_id = fallback
    db.flush()
    return row


def _find_first_active(db: Session, *, provider: str, market_id: int | None) -> ChannelAccount | None:
    query = db.query(ChannelAccount).filter(
        ChannelAccount.provider == provider,
        ChannelAccount.is_active.is_(True),
    )
    if market_id is not None:
        row = query.filter(ChannelAccount.market_id == market_id).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()
        if row is not None:
            return row
    return query.filter(ChannelAccount.market_id.is_(None)).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).first()


def explain_route(
    db: Session,
    *,
    provider: str | None = None,
    market_id: int | None = None,
    requested_account_id: str | None = None,
    ticket_id: int | None = None,
) -> tuple[ChannelAccount | None, ChannelAccount | None, list[str], dict]:
    steps: list[str] = []
    context = {
        "ticket_id": ticket_id,
        "provider": provider,
        "market_id": market_id,
        "requested_account_id": requested_account_id,
    }
    ticket = None
    if ticket_id is not None:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        provider = provider or str(ticket.source_channel.value if hasattr(ticket.source_channel, "value") else ticket.source_channel)
        market_id = market_id if market_id is not None else ticket.market_id
        if ticket.channel_account_id:
            bound = db.query(ChannelAccount).filter(ChannelAccount.id == ticket.channel_account_id).first()
            if bound is not None:
                steps.append("hit=ticket.channel_account_id")
                fallback = db.query(ChannelAccount).filter(ChannelAccount.account_id == bound.fallback_account_id).first() if bound.fallback_account_id else None
                context["ticket_channel_account_id"] = ticket.channel_account_id
                return bound, fallback, steps, context

    normalized_provider = normalize_provider(provider or "whatsapp")
    context["provider"] = normalized_provider
    context["market_id"] = market_id

    requested = normalize_text(requested_account_id)
    if requested:
        row = db.query(ChannelAccount).filter(ChannelAccount.account_id == requested, ChannelAccount.provider == normalized_provider, ChannelAccount.is_active.is_(True)).first()
        if row is not None:
            steps.append("hit=requested_account_id")
            fallback = db.query(ChannelAccount).filter(ChannelAccount.account_id == row.fallback_account_id).first() if row.fallback_account_id else None
            return row, fallback, steps, context
        steps.append("miss=requested_account_id")

    selected = _find_first_active(db, provider=normalized_provider, market_id=market_id)
    if selected is None:
        steps.append("miss=no_active_account")
        return None, None, steps, context
    if market_id is not None and selected.market_id == market_id:
        steps.append("hit=market_specific_lowest_priority")
    else:
        steps.append("hit=global_lowest_priority")
    fallback = db.query(ChannelAccount).filter(ChannelAccount.account_id == selected.fallback_account_id).first() if selected.fallback_account_id else None
    if fallback is not None:
        steps.append("fallback=available")
    else:
        steps.append("fallback=none")
    return selected, fallback, steps, context


def list_onboarding_tasks(db: Session, *, provider: str | None = None, limit: int = 20) -> list[ChannelOnboardingTask]:
    query = db.query(ChannelOnboardingTask).order_by(ChannelOnboardingTask.created_at.desc())
    if provider:
        query = query.filter(ChannelOnboardingTask.provider == normalize_provider(provider))
    return query.limit(limit).all()


def create_onboarding_task(db: Session, payload, actor_id: int | None) -> ChannelOnboardingTask:
    provider = normalize_provider(payload.provider)
    ensure_market(db, payload.market_id)
    row = ChannelOnboardingTask(
        provider=provider,
        status="pending",
        requested_by=actor_id,
        market_id=payload.market_id,
        target_slot=normalize_text(payload.target_slot),
        desired_display_name=normalize_text(payload.desired_display_name),
        desired_channel_account_binding=normalize_text(payload.desired_channel_account_binding),
        openclaw_account_id=normalize_text(payload.openclaw_account_id),
        last_error=None,
    )
    db.add(row)
    db.flush()
    return row


def update_onboarding_task(db: Session, row: ChannelOnboardingTask, payload) -> ChannelOnboardingTask:
    values = payload.model_dump(exclude_unset=True)
    if "status" in values:
        status = values["status"]
        if status not in ALLOWED_ONBOARDING_STATUSES:
            raise HTTPException(status_code=400, detail="Unsupported onboarding status")
        row.status = status
        now = datetime.utcnow()
        if status == "running" and row.started_at is None:
            row.started_at = now
        if status in {"success", "failed", "cancelled"}:
            row.completed_at = now
    for field in ("openclaw_account_id", "last_error", "desired_display_name", "desired_channel_account_binding", "target_slot"):
        if field in values:
            setattr(row, field, normalize_text(values[field]))
    db.flush()
    return row
