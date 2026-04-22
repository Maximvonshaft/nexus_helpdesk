from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount
from ..models_control_plane import ChannelOnboardingTask
from ..services.audit_service import log_admin_audit
from ..services.channel_control_service import create_account, create_onboarding_task, explain_route, list_accounts, list_onboarding_tasks, update_account, update_onboarding_task
from ..services.permissions import ensure_can_manage_channel_accounts
from ..unit_of_work import managed_session
from .deps import get_current_user


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_dt(self, value: Any):
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class ChannelAccountRead(APIModel):
    id: int
    provider: str
    account_id: str
    display_name: str | None = None
    market_id: int | None = None
    is_active: bool
    priority: int
    fallback_account_id: str | None = None
    health_status: str
    last_health_check_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChannelAccountCreate(BaseModel):
    provider: str
    account_id: str
    display_name: str | None = None
    market_id: int | None = None
    is_active: bool = True
    priority: int = 100
    fallback_account_id: str | None = None


class ChannelAccountUpdate(BaseModel):
    display_name: str | None = None
    market_id: int | None = None
    is_active: bool | None = None
    priority: int | None = None
    fallback_account_id: str | None = None


class ChannelRouteExplainRequest(BaseModel):
    provider: str | None = None
    market_id: int | None = None
    requested_account_id: str | None = None
    ticket_id: int | None = None


class ChannelRouteExplainRead(BaseModel):
    selected_account: ChannelAccountRead | None = None
    fallback_account: ChannelAccountRead | None = None
    debug_steps: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class ChannelOnboardingTaskRead(APIModel):
    id: int
    provider: str
    status: str
    requested_by: int | None = None
    market_id: int | None = None
    target_slot: str | None = None
    desired_display_name: str | None = None
    desired_channel_account_binding: str | None = None
    openclaw_account_id: str | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ChannelOnboardingTaskCreate(BaseModel):
    provider: str
    market_id: int | None = None
    target_slot: str | None = None
    desired_display_name: str | None = None
    desired_channel_account_binding: str | None = None
    openclaw_account_id: str | None = None


class ChannelOnboardingTaskUpdate(BaseModel):
    status: str | None = None
    target_slot: str | None = None
    desired_display_name: str | None = None
    desired_channel_account_binding: str | None = None
    openclaw_account_id: str | None = None
    last_error: str | None = None


router = APIRouter(prefix="/api/admin/channel-control", tags=["admin-channel-control"])


@router.get("/accounts", response_model=list[ChannelAccountRead])
def get_channel_accounts(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    return [ChannelAccountRead.model_validate(row) for row in list_accounts(db)]


@router.post("/accounts", response_model=ChannelAccountRead)
def post_channel_account(payload: ChannelAccountCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    with managed_session(db):
        row = create_account(db, payload)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="channel_control.account.create",
            target_type="channel_account",
            target_id=row.id,
            old_value=None,
            new_value={"provider": row.provider, "account_id": row.account_id, "market_id": row.market_id, "priority": row.priority},
        )
    db.refresh(row)
    return ChannelAccountRead.model_validate(row)


@router.patch("/accounts/{account_id}", response_model=ChannelAccountRead)
def patch_channel_account(account_id: int, payload: ChannelAccountUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(ChannelAccount).filter(ChannelAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    before = {"display_name": row.display_name, "market_id": row.market_id, "is_active": row.is_active, "priority": row.priority, "fallback_account_id": row.fallback_account_id}
    with managed_session(db):
        row = update_account(db, row, payload)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="channel_control.account.update",
            target_type="channel_account",
            target_id=row.id,
            old_value=before,
            new_value={"display_name": row.display_name, "market_id": row.market_id, "is_active": row.is_active, "priority": row.priority, "fallback_account_id": row.fallback_account_id},
        )
    db.refresh(row)
    return ChannelAccountRead.model_validate(row)


@router.post("/route-explain", response_model=ChannelRouteExplainRead)
def post_route_explain(payload: ChannelRouteExplainRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    selected, fallback, steps, context = explain_route(
        db,
        provider=payload.provider,
        market_id=payload.market_id,
        requested_account_id=payload.requested_account_id,
        ticket_id=payload.ticket_id,
    )
    return ChannelRouteExplainRead(
        selected_account=ChannelAccountRead.model_validate(selected) if selected else None,
        fallback_account=ChannelAccountRead.model_validate(fallback) if fallback else None,
        debug_steps=steps,
        context=context,
    )


@router.get("/onboarding-tasks", response_model=list[ChannelOnboardingTaskRead])
def get_onboarding_tasks(provider: str | None = None, limit: int = 20, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    return [ChannelOnboardingTaskRead.model_validate(row) for row in list_onboarding_tasks(db, provider=provider, limit=limit)]


@router.post("/onboarding-tasks", response_model=ChannelOnboardingTaskRead)
def post_onboarding_task(payload: ChannelOnboardingTaskCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    with managed_session(db):
        row = create_onboarding_task(db, payload, getattr(current_user, "id", None))
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="channel_control.onboarding.create",
            target_type="channel_onboarding_task",
            target_id=row.id,
            old_value=None,
            new_value={"provider": row.provider, "market_id": row.market_id, "target_slot": row.target_slot, "desired_display_name": row.desired_display_name},
        )
    db.refresh(row)
    return ChannelOnboardingTaskRead.model_validate(row)


@router.patch("/onboarding-tasks/{task_id}", response_model=ChannelOnboardingTaskRead)
def patch_onboarding_task(task_id: int, payload: ChannelOnboardingTaskUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(ChannelOnboardingTask).filter(ChannelOnboardingTask.id == task_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Onboarding task not found")
    before = {"status": row.status, "openclaw_account_id": row.openclaw_account_id, "last_error": row.last_error}
    with managed_session(db):
        row = update_onboarding_task(db, row, payload)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="channel_control.onboarding.update",
            target_type="channel_onboarding_task",
            target_id=row.id,
            old_value=before,
            new_value={"status": row.status, "openclaw_account_id": row.openclaw_account_id, "last_error": row.last_error},
        )
    db.refresh(row)
    return ChannelOnboardingTaskRead.model_validate(row)
