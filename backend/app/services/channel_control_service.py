from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Market
from ..models_control_plane import ChannelOnboardingTask
from ..utils.time import utc_now

ALLOWED_PROVIDERS = {"whatsapp", "email", "web_chat", "telegram", "sms"}
ALLOWED_STATUSES = {"pending", "in_progress", "completed", "failed", "cancelled"}
TERMINAL_STATUSES = {"completed", "cancelled"}


def _normalize_provider(value: str) -> str:
    provider = value.strip().lower().replace("-", "_")
    if provider == "web":
        provider = "web_chat"
    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported channel provider")
    return provider


def _ensure_market_exists(db: Session, market_id: Optional[int]) -> None:
    if market_id is None:
        return
    if db.query(Market).filter(Market.id == market_id, Market.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=400, detail="Market not found or inactive")


def _ensure_mutable(row: ChannelOnboardingTask) -> None:
    if row.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Terminal task cannot be modified")


def list_tasks(
    db: Session,
    *,
    provider: Optional[str] = None,
    status: Optional[str] = None,
    market_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ChannelOnboardingTask], int]:
    query = db.query(ChannelOnboardingTask)
    if provider:
        query = query.filter(ChannelOnboardingTask.provider == _normalize_provider(provider))
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="Unsupported task status")
        query = query.filter(ChannelOnboardingTask.status == normalized_status)
    if market_id is not None:
        query = query.filter(ChannelOnboardingTask.market_id == market_id)
    total = query.count()
    rows = query.order_by(ChannelOnboardingTask.created_at.desc(), ChannelOnboardingTask.id.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return rows, total


def get_task_or_404(db: Session, task_id: int) -> ChannelOnboardingTask:
    row = db.query(ChannelOnboardingTask).filter(ChannelOnboardingTask.id == task_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Channel onboarding task not found")
    return row


def create_task(db: Session, payload, actor) -> ChannelOnboardingTask:
    provider = _normalize_provider(payload.provider)
    _ensure_market_exists(db, payload.market_id)
    row = ChannelOnboardingTask(
        provider=provider,
        status="pending",
        requested_by=getattr(actor, "id", None),
        market_id=payload.market_id,
        target_slot=payload.target_slot,
        desired_display_name=payload.desired_display_name,
        desired_channel_account_binding=payload.desired_channel_account_binding,
        openclaw_account_id=payload.openclaw_account_id,
    )
    db.add(row)
    db.flush()
    return row


def update_task(db: Session, row: ChannelOnboardingTask, payload) -> ChannelOnboardingTask:
    _ensure_mutable(row)
    values = payload.model_dump(exclude_unset=True)
    _ensure_market_exists(db, values.get("market_id", row.market_id))
    for key, value in values.items():
        setattr(row, key, value)
    db.flush()
    return row


def mark_in_progress(db: Session, row: ChannelOnboardingTask) -> ChannelOnboardingTask:
    _ensure_mutable(row)
    if row.status == "in_progress":
        return row
    if row.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending tasks can start")
    row.status = "in_progress"
    row.started_at = row.started_at or utc_now()
    row.last_error = None
    db.flush()
    return row


def complete_task(db: Session, row: ChannelOnboardingTask, payload) -> ChannelOnboardingTask:
    _ensure_mutable(row)
    if row.status not in {"pending", "in_progress", "failed"}:
        raise HTTPException(status_code=400, detail="Task cannot be completed from current status")
    if payload.openclaw_account_id is not None:
        row.openclaw_account_id = payload.openclaw_account_id
    if payload.desired_channel_account_binding is not None:
        row.desired_channel_account_binding = payload.desired_channel_account_binding
    row.status = "completed"
    row.started_at = row.started_at or utc_now()
    row.completed_at = utc_now()
    row.last_error = None
    db.flush()
    return row


def fail_task(db: Session, row: ChannelOnboardingTask, payload) -> ChannelOnboardingTask:
    _ensure_mutable(row)
    if row.status not in {"pending", "in_progress", "failed"}:
        raise HTTPException(status_code=400, detail="Task cannot be failed from current status")
    row.status = "failed"
    row.started_at = row.started_at or utc_now()
    row.last_error = payload.last_error
    db.flush()
    return row


def cancel_task(db: Session, row: ChannelOnboardingTask) -> ChannelOnboardingTask:
    _ensure_mutable(row)
    row.status = "cancelled"
    row.completed_at = utc_now()
    db.flush()
    return row
