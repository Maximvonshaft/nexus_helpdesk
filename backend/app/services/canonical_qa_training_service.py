"""Canonical QA training service.

This facade applies the actor's canonical Tenant boundary to the complete QA
read/write transaction, then delegates product scoring and presentation to the
private core. It does not create a second QA model or duplicate queue authority.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import HTTPException, status
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

from ..models import AdminAuditLog, Ticket, User
from ..operator_models import OperatorTask
from ..voice_models import WebchatVoiceSession
from . import qa_training_service_core as _core
from .agent_resource_authority import (
    AI_CONFIG_RESOURCE,
    bind_resource,
    bind_session_actor,
)
from .tenant_query_authority import (
    ActorTenantQueryScope,
    TenantQueryScopeError,
    actor_tenant_query_scope,
)
from .voice_duration import voice_talk_duration_seconds


@contextmanager
def _canonical_qa_scope(
    db: Session,
    current_user: User,
) -> Iterator[ActorTenantQueryScope]:
    """Apply one fail-closed Tenant boundary to every QA ORM select.

    A per-Session listener keeps concurrent requests isolated. Ticketless QA
    tasks remain visible only when their own ``tenant_id`` matches. System audit
    rows without a Tenant-bound actor are intentionally excluded from a Tenant
    operator's metrics.
    """

    bind_session_actor(db, current_user)
    try:
        scope = actor_tenant_query_scope(
            db,
            current_user,
            require_bound_tenant=True,
        )
    except TenantQueryScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="qa_training_tenant_required",
        ) from exc
    if scope.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="qa_training_tenant_required",
        )

    tenant_id = int(scope.tenant_id)
    tenant_actor_ids = select(User.id).where(User.tenant_id == tenant_id)

    def _enforce_tenant(execute_state) -> None:  # noqa: ANN001
        if (
            not execute_state.is_select
            or execute_state.is_column_load
            or execute_state.is_relationship_load
        ):
            return
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                Ticket,
                Ticket.tenant_id == tenant_id,
                include_aliases=True,
            ),
            with_loader_criteria(
                OperatorTask,
                OperatorTask.tenant_id == tenant_id,
                include_aliases=True,
            ),
            with_loader_criteria(
                AdminAuditLog,
                AdminAuditLog.actor_id.in_(tenant_actor_ids),
                include_aliases=True,
            ),
        )

    event.listen(db, "do_orm_execute", _enforce_tenant)
    try:
        yield scope
    finally:
        event.remove(db, "do_orm_execute", _enforce_tenant)


def _attach_voice_duration_evidence(
    db: Session,
    payload: dict[str, Any],
) -> None:
    qa_rows = payload.get("qa_queue")
    if not isinstance(qa_rows, list):
        return
    public_ids = {
        str(row.get("key"))[len("webcall:") :]
        for row in qa_rows
        if isinstance(row, dict) and str(row.get("key") or "").startswith("webcall:")
    }
    if not public_ids:
        return
    sessions = (
        db.query(WebchatVoiceSession)
        .join(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
        .filter(WebchatVoiceSession.public_id.in_(sorted(public_ids)))
        .all()
    )
    durations = {
        session.public_id: voice_talk_duration_seconds(session)
        for session in sessions
    }
    for row in qa_rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        if not key.startswith("webcall:"):
            continue
        duration = durations.get(key[len("webcall:") :])
        if duration is None:
            continue
        evidence = row.setdefault("evidence", [])
        if isinstance(evidence, list):
            marker = f"talk duration {duration}s"
            if marker not in evidence:
                evidence.append(marker)


def build_qa_training(db: Session, current_user: User) -> dict[str, Any]:
    with _canonical_qa_scope(db, current_user):
        payload = _core.build_qa_training(db, current_user)
        _attach_voice_duration_evidence(db, payload)
        return payload


def submit_knowledge_gap(db: Session, current_user: User, payload) -> dict[str, Any]:
    with _canonical_qa_scope(db, current_user) as scope:
        result = _core.submit_knowledge_gap(db, current_user, payload)
        resource_id = result.get("resource_id")
        if resource_id is None:
            raise RuntimeError("qa_knowledge_gap_resource_missing")
        bind_resource(
            db,
            resource_type=AI_CONFIG_RESOURCE,
            resource_id=int(resource_id),
            tenant_key=scope.tenant_key,
            actor_id=current_user.id,
        )
        return result


def submit_agent_appeal(db: Session, current_user: User, payload) -> dict[str, Any]:
    with _canonical_qa_scope(db, current_user):
        return _core.submit_agent_appeal(db, current_user, payload)


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "build_qa_training",
    "submit_agent_appeal",
    "submit_knowledge_gap",
]
