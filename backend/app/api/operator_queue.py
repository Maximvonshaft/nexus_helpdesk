from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.operator_queue import list_operator_tasks, project_openclaw_unresolved_events, project_webchat_handoff_tasks, serialize_operator_task, transition_operator_task
from ..services.openclaw_bridge import replay_unresolved_openclaw_event as replay_unresolved_openclaw_event_payload
from ..services.permissions import ensure_can_manage_runtime
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/operator-queue", tags=["operator-queue"])


class OperatorTaskTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


@router.get("")
def get_operator_queue(
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        projected_openclaw = project_openclaw_unresolved_events(db, limit=100)
        projected_webchat = project_webchat_handoff_tasks(db, limit=100)
        result = list_operator_tasks(db, status=status, source_type=source_type, task_type=task_type, cursor=cursor, limit=limit)
    result["projected_openclaw_unresolved"] = projected_openclaw
    result["projected_webchat_handoff"] = projected_webchat
    return result


@router.post("/{task_id}/assign")
def assign_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="assign", actor_id=current_user.id)
        return serialize_operator_task(row)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/resolve")
def resolve_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="resolve", actor_id=current_user.id)
        return serialize_operator_task(row)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/drop")
def drop_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="drop", actor_id=current_user.id)
        return serialize_operator_task(row)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/replay")
def replay_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="replay", actor_id=current_user.id)
            replay_result = None
            if row.unresolved_event_id:
                replay_result = replay_unresolved_openclaw_event_payload(db, row.unresolved_event_id)
        result = serialize_operator_task(row)
        result["replay_result"] = replay_result
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
