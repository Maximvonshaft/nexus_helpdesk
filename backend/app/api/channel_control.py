from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_channel_control import (
    ChannelOnboardingTaskCompleteRequest,
    ChannelOnboardingTaskCreate,
    ChannelOnboardingTaskFailRequest,
    ChannelOnboardingTaskListOut,
    ChannelOnboardingTaskOut,
    ChannelOnboardingTaskUpdate,
)
from ..services.permissions import ensure_can_manage_channel_accounts, ensure_can_manage_runtime
from ..services import channel_control_service
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/channel-control", tags=["channel-control"])


def _task_out(row) -> ChannelOnboardingTaskOut:
    return ChannelOnboardingTaskOut.model_validate(row)


def _ensure_channel_control_access(user, db: Session) -> None:
    try:
        ensure_can_manage_channel_accounts(user, db)
    except Exception:
        ensure_can_manage_runtime(user, db)


@router.get("/onboarding-tasks", response_model=ChannelOnboardingTaskListOut)
def list_onboarding_tasks(
    provider: str | None = None,
    status: str | None = None,
    market_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    rows, total = channel_control_service.list_tasks(
        db,
        provider=provider,
        status=status,
        market_id=market_id,
        limit=limit,
        offset=offset,
    )
    return ChannelOnboardingTaskListOut(tasks=[_task_out(row) for row in rows], total=total)


@router.post("/onboarding-tasks", response_model=ChannelOnboardingTaskOut)
def create_onboarding_task(
    payload: ChannelOnboardingTaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    with managed_session(db):
        row = channel_control_service.create_task(db, payload, current_user)
    db.refresh(row)
    return _task_out(row)


@router.get("/onboarding-tasks/{task_id}", response_model=ChannelOnboardingTaskOut)
def get_onboarding_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    return _task_out(channel_control_service.get_task_or_404(db, task_id))


@router.patch("/onboarding-tasks/{task_id}", response_model=ChannelOnboardingTaskOut)
def update_onboarding_task(
    task_id: int,
    payload: ChannelOnboardingTaskUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    row = channel_control_service.get_task_or_404(db, task_id)
    with managed_session(db):
        row = channel_control_service.update_task(db, row, payload)
    db.refresh(row)
    return _task_out(row)


@router.post("/onboarding-tasks/{task_id}/start", response_model=ChannelOnboardingTaskOut)
def start_onboarding_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    row = channel_control_service.get_task_or_404(db, task_id)
    with managed_session(db):
        row = channel_control_service.mark_in_progress(db, row)
    db.refresh(row)
    return _task_out(row)


@router.post("/onboarding-tasks/{task_id}/complete", response_model=ChannelOnboardingTaskOut)
def complete_onboarding_task(
    task_id: int,
    payload: ChannelOnboardingTaskCompleteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    row = channel_control_service.get_task_or_404(db, task_id)
    with managed_session(db):
        row = channel_control_service.complete_task(db, row, payload)
    db.refresh(row)
    return _task_out(row)


@router.post("/onboarding-tasks/{task_id}/fail", response_model=ChannelOnboardingTaskOut)
def fail_onboarding_task(
    task_id: int,
    payload: ChannelOnboardingTaskFailRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    row = channel_control_service.get_task_or_404(db, task_id)
    with managed_session(db):
        row = channel_control_service.fail_task(db, row, payload)
    db.refresh(row)
    return _task_out(row)


@router.post("/onboarding-tasks/{task_id}/cancel", response_model=ChannelOnboardingTaskOut)
def cancel_onboarding_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_channel_control_access(current_user, db)
    row = channel_control_service.get_task_or_404(db, task_id)
    with managed_session(db):
        row = channel_control_service.cancel_task(db, row)
    db.refresh(row)
    return _task_out(row)
