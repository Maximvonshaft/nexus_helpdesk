from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..operator_schemas import (
    OperatorQueueCurrentScopesResponse,
    OperatorQueueProjectResponse,
    OperatorQueueScopeGrantRead,
    OperatorQueueScopeGrantUpsert,
    OperatorTaskListResponse,
    OperatorTaskTransitionRequest,
    OperatorTaskTransitionResponse,
    UnifiedOperatorQueueResponse,
)
from ..operator_models import OperatorQueueScopeGrant
from ..services.operator_queue import (
    OperatorQueueError,
    list_operator_tasks,
    project_operator_queue,
    serialize_operator_task,
    transition_operator_task,
)
from ..services.operator_queue_scope import (
    delete_scope_grant,
    list_current_scope_grants,
    serialize_scope_grant,
    upsert_scope_grant,
)
from ..services.canonical_operator_work_queue import list_unified_operator_queue
from ..services.permissions import ensure_can_manage_runtime, ensure_can_manage_users
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/operator-queue", tags=["operator-queue"])


def _raise_operator_queue_error(exc: OperatorQueueError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.detail}) from exc


def _optional_query_string(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _query_limit(value) -> int:
    return value if isinstance(value, int) else 50


@router.get("/unified", response_model=UnifiedOperatorQueueResponse)
def get_unified_operator_queue(
    x_nexus_tenant: str = Header(..., alias="X-Nexus-Tenant", min_length=1, max_length=80),
    country_code: str = Query(..., min_length=2, max_length=16),
    channel_key: str = Query(..., min_length=1, max_length=40),
    state: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    sla: str | None = Query(default=None),
    retry: str | None = Query(default=None),
    sort: str = Query(default="oldest"),
    cursor: str | None = Query(default=None, max_length=2048),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_unified_operator_queue(
        db,
        current_user=current_user,
        tenant_key=x_nexus_tenant,
        country_code=country_code,
        channel_key=channel_key,
        state=_optional_query_string(state),
        source_type=_optional_query_string(source_type),
        owner=_optional_query_string(owner),
        priority=_optional_query_string(priority),
        sla=_optional_query_string(sla),
        retry=_optional_query_string(retry),
        sort=sort if isinstance(sort, str) else "oldest",
        cursor=_optional_query_string(cursor),
        limit=_query_limit(limit),
    )


@router.get("/my-scopes", response_model=OperatorQueueCurrentScopesResponse)
def get_current_operator_queue_scopes(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_current_scope_grants(db, current_user=current_user)


@router.get("/scope-grants", response_model=list[OperatorQueueScopeGrantRead])
def get_operator_queue_scope_grants(
    user_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    query = db.query(OperatorQueueScopeGrant)
    if isinstance(user_id, int):
        query = query.filter(OperatorQueueScopeGrant.user_id == user_id)
    rows = query.order_by(OperatorQueueScopeGrant.user_id.asc(), OperatorQueueScopeGrant.id.asc()).limit(1000).all()
    return [serialize_scope_grant(row) for row in rows]


@router.put("/scope-grants", response_model=OperatorQueueScopeGrantRead)
def put_operator_queue_scope_grant(
    payload: OperatorQueueScopeGrantUpsert,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    with managed_session(db):
        row = upsert_scope_grant(db, current_user=current_user, payload=payload)
    db.refresh(row)
    return serialize_scope_grant(row)


@router.delete("/scope-grants/{grant_id}")
def remove_operator_queue_scope_grant(
    grant_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    with managed_session(db):
        delete_scope_grant(db, current_user=current_user, grant_id=grant_id)
    return {"ok": True}


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
