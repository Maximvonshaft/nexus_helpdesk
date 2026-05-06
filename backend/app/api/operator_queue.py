from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..operator_schemas import (
    OperatorQueueProjectResponse,
    OperatorTaskListResponse,
    OperatorTaskTransitionRequest,
    OperatorTaskTransitionResponse,
)
from ..services.openclaw_bridge import replay_unresolved_openclaw_event
from ..services.operator_queue import (
    OperatorQueueError,
    list_operator_tasks,
    project_operator_queue,
    replay_operator_task,
    serialize_operator_task,
    transition_operator_task,
)
from ..services.permissions import ensure_can_manage_runtime
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/operator-queue", tags=["operator-queue"])


def _raise_operator_queue_error(exc: OperatorQueueError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.detail}) from exc


def _optional_query_string(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _query_limit(value) -> int:
    return value if isinstance(value, int) else 50


@router.get("", response_model=OperatorTaskListResponse)
def get_operator_queue(
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        return list_operator_tasks(
            db,
            status=_optional_query_string(status),
            source_type=_optional_query_string(source_type),
            task_type=_optional_query_string(task_type),
            cursor=_optional_query_string(cursor),
            limit=_query_limit(limit),
        )
    except OperatorQueueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/project", response_model=OperatorQueueProjectResponse)
def project_operator_queue_endpoint(
    payload: OperatorTaskTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        return project_operator_queue(db, actor_id=current_user.id, note=payload.note if payload else None)


@router.post("/{task_id}/assign", response_model=OperatorTaskTransitionResponse)
def assign_operator_task(
    task_id: int,
    payload: OperatorTaskTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="assign", actor_id=current_user.id, note=payload.note if payload else None)
        return {"task": serialize_operator_task(row), "replay_result": None}
    except OperatorQueueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/{task_id}/resolve", response_model=OperatorTaskTransitionResponse)
def resolve_operator_task(
    task_id: int,
    payload: OperatorTaskTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="resolve", actor_id=current_user.id, note=payload.note if payload else None)
        return {"task": serialize_operator_task(row), "replay_result": None}
    except OperatorQueueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/{task_id}/drop", response_model=OperatorTaskTransitionResponse)
def drop_operator_task(
    task_id: int,
    payload: OperatorTaskTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row = transition_operator_task(db, task_id=task_id, action="drop", actor_id=current_user.id, note=payload.note if payload else None)
        return {"task": serialize_operator_task(row), "replay_result": None}
    except OperatorQueueError as exc:
        _raise_operator_queue_error(exc)


@router.post("/{task_id}/replay", response_model=OperatorTaskTransitionResponse)
def replay_operator_task_endpoint(
    task_id: int,
    payload: OperatorTaskTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        with managed_session(db):
            row, replay_result = replay_operator_task(
                db,
                task_id=task_id,
                actor_id=current_user.id,
                note=payload.note if payload else None,
                replay_func=replay_unresolved_openclaw_event,
            )
        return {"task": serialize_operator_task(row), "replay_result": replay_result}
    except OperatorQueueError as exc:
        _raise_operator_queue_error(exc)
