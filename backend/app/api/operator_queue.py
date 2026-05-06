from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import OpenClawUnresolvedEvent
from ..operator_models import OperatorTask
from ..services.audit_service import log_admin_audit
from ..services.openclaw_bridge import replay_unresolved_openclaw_event as replay_unresolved_openclaw_event_payload
from ..services.operator_queue import (
    list_operator_tasks,
    mark_operator_task_replay_failed,
    mark_operator_task_replaying,
    project_openclaw_unresolved_events,
    project_webchat_handoff_tasks,
    serialize_operator_task,
    transition_operator_task,
)
from ..services.permissions import ensure_can_manage_runtime
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/operator-queue", tags=["operator-queue"])


class OperatorTaskTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


def _request_note(payload: OperatorTaskTransitionRequest | None) -> str | None:
    if payload is None or payload.note is None:
        return None
    note = payload.note.strip()
    return note[:1000] if note else None


def _raise_operator_queue_error(exc: ValueError) -> None:
    code = str(exc)
    if code == "operator_task_not_found":
        raise HTTPException(status_code=404, detail=code) from exc
    if code == "unresolved_event_missing":
        raise HTTPException(status_code=404, detail=code) from exc
    if code in {"unsupported_operator_task_action", "operator_task_not_replayable"}:
        raise HTTPException(status_code=400, detail=code) from exc
    raise HTTPException(status_code=400, detail=code or "operator_queue_error") from exc


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
    return list_operator_tasks(db, status=status, source_type=source_type, task_type=task_type, cursor=cursor, limit=limit)


@router.post("/project")
def project_operator_queue(payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    note = _request_note(payload)
    with managed_session(db):
        projected_openclaw = project_openclaw_unresolved_events(db, limit=100)
        projected_webchat = project_webchat_handoff_tasks(db, limit=100)
        result = {
            "projected_openclaw_unresolved": projected_openclaw,
            "projected_webchat_handoff": projected_webchat,
        }
        new_value = dict(result)
        if note:
            new_value["note"] = note
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="operator_queue.project",
            target_type="operator_queue",
            target_id=None,
            old_value=None,
            new_value=new_value,
        )
    return result


@router.post("/{task_id}/assign")
def assign_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="assign", actor_id=current_user.id, note=_request_note(payload))
        return serialize_operator_task(row)
    except ValueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/{task_id}/resolve")
def resolve_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="resolve", actor_id=current_user.id, note=_request_note(payload))
        return serialize_operator_task(row)
    except ValueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/{task_id}/drop")
def drop_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="drop", actor_id=current_user.id, note=_request_note(payload))
        return serialize_operator_task(row)
    except ValueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/{task_id}/replay")
def replay_operator_task(task_id: int, payload: OperatorTaskTransitionRequest | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    note = _request_note(payload)
    replay_ok = False
    result = None
    try:
        with managed_session(db):
            row = db.query(OperatorTask).filter(OperatorTask.id == task_id).first()
            if row is None:
                raise ValueError("operator_task_not_found")
            if not row.unresolved_event_id:
                raise ValueError("operator_task_not_replayable")

            event_row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == row.unresolved_event_id).first()
            if event_row is None:
                raise ValueError("unresolved_event_missing")

            mark_operator_task_replaying(db, row=row, actor_id=current_user.id, note=note)

            replay_error = None
            try:
                replay_ok = replay_unresolved_openclaw_event_payload(db, row=event_row)
            except Exception as exc:  # replay is a controlled mutation; persist the failed attempt before returning 409.
                replay_ok = False
                replay_error = f"{type(exc).__name__}: {exc}"[:500]
                event_row.status = "failed"
                event_row.last_error = replay_error
                event_row.updated_at = utc_now()

            if replay_ok:
                row = transition_operator_task(db, task_id=task_id, action="replay", actor_id=current_user.id, note=note)
                result = serialize_operator_task(row)
                result["replay_result"] = True
            else:
                row = mark_operator_task_replay_failed(
                    db,
                    row=row,
                    event_row=event_row,
                    actor_id=current_user.id,
                    note=note,
                    error=replay_error,
                )
                result = serialize_operator_task(row)
                result["replay_result"] = False
                result["replay_error"] = replay_error or event_row.last_error or "openclaw_replay_failed"
    except ValueError as exc:
        _raise_operator_queue_error(exc)

    if not replay_ok:
        raise HTTPException(status_code=409, detail=result or {"replay_result": False, "replay_error": "openclaw_replay_failed"})
    return result
