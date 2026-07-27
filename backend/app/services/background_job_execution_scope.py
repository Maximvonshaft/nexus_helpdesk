from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Iterable

from sqlalchemy import and_, case, exists, or_, select, update
from sqlalchemy.orm import Session

from ..enums import JobStatus
from ..models import BackgroundJob
from ..models_job_scope import BackgroundJobScope
from ..settings import get_settings
from ..utils.time import utc_now
from .background_job_scope import (
    PLATFORM_JOB_TYPES,
    PURPOSE_BY_JOB_TYPE,
    SCOPE_SCHEMA,
    derive_job_scope_values,
)
from .data_subject_action_service import ensure_data_processing_allowed


class BackgroundJobExecutionScopeError(RuntimeError):
    pass


def _expected_purpose_expression():
    return case(
        PURPOSE_BY_JOB_TYPE,
        value=BackgroundJob.job_type,
        else_="unclassified",
    )


def executable_background_job_scope_filter():
    """Return the sole SQL predicate for an automatically executable Job.

    Unknown, missing, unresolved, stale-schema and purpose-mismatched envelopes
    fail closed. Platform execution is unavailable until a Job type is explicitly
    allow-listed by the canonical BackgroundJob scope authority.
    """

    tenant_scope = and_(
        BackgroundJobScope.scope_type == "tenant",
        BackgroundJobScope.tenant_id.is_not(None),
    )
    if PLATFORM_JOB_TYPES:
        platform_scope = and_(
            BackgroundJobScope.scope_type == "platform",
            BackgroundJobScope.tenant_id.is_(None),
            BackgroundJob.job_type.in_(tuple(sorted(PLATFORM_JOB_TYPES))),
        )
        ownership_scope = or_(tenant_scope, platform_scope)
    else:
        ownership_scope = tenant_scope
    return and_(
        BackgroundJobScope.job_id == BackgroundJob.id,
        BackgroundJobScope.source_schema == SCOPE_SCHEMA,
        BackgroundJobScope.purpose == _expected_purpose_expression(),
        BackgroundJobScope.purpose != "unclassified",
        ownership_scope,
    )


def _pending_filters(now, *, job_types: tuple[str, ...]):
    settings = get_settings()
    lock_deadline = now - timedelta(seconds=settings.job_lock_seconds)
    due = or_(
        BackgroundJob.next_run_at.is_(None),
        BackgroundJob.next_run_at <= now,
    )
    stale_processing = and_(
        BackgroundJob.status == JobStatus.processing,
        or_(
            BackgroundJob.locked_at.is_(None),
            BackgroundJob.locked_at < lock_deadline,
        ),
    )
    filters = [
        or_(
            and_(BackgroundJob.status == JobStatus.pending, due),
            stale_processing,
        )
    ]
    if job_types:
        filters.append(BackgroundJob.job_type.in_(job_types))
    return filters


def claim_executable_background_jobs(
    db: Session,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
    job_types: Iterable[str] | None = None,
) -> list[BackgroundJob]:
    """Atomically claim only Jobs with a currently executable Scope envelope."""

    settings = get_settings()
    bounded_limit = max(1, min(int(limit or settings.job_batch_size), 1000))
    lease_token = worker_id or f"job-worker-{uuid.uuid4().hex[:8]}"
    normalized_types = tuple(
        sorted({str(item).strip() for item in (job_types or ()) if str(item).strip()})
    )
    now = utc_now()
    pending_filters = _pending_filters(now, job_types=normalized_types)
    scope_filter = executable_background_job_scope_filter()

    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        rows = db.execute(
            select(BackgroundJob.id)
            .join(
                BackgroundJobScope,
                BackgroundJobScope.job_id == BackgroundJob.id,
            )
            .where(*pending_filters, scope_filter)
            .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
            .limit(bounded_limit)
            .with_for_update(skip_locked=True)
        ).all()
        claimed_ids = [int(row[0]) for row in rows]
        if not claimed_ids:
            db.rollback()
            return []
        result = db.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id.in_(claimed_ids),
                *pending_filters,
                exists(
                    select(1).where(
                        BackgroundJobScope.job_id == BackgroundJob.id,
                        executable_background_job_scope_filter(),
                    )
                ),
            )
            .values(
                status=JobStatus.processing,
                locked_at=now,
                locked_by=lease_token,
                updated_at=now,
            )
        )
        if result.rowcount != len(claimed_ids):
            db.rollback()
            raise BackgroundJobExecutionScopeError(
                "background_job_scope_changed_during_claim"
            )
        db.commit()
    else:
        candidate_ids = [
            int(row[0])
            for row in db.execute(
                select(BackgroundJob.id)
                .join(
                    BackgroundJobScope,
                    BackgroundJobScope.job_id == BackgroundJob.id,
                )
                .where(*pending_filters, scope_filter)
                .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
                .limit(bounded_limit)
            ).all()
        ]
        claimed_ids: list[int] = []
        for job_id in candidate_ids:
            result = db.execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    *pending_filters,
                    exists(
                        select(1).where(
                            BackgroundJobScope.job_id == BackgroundJob.id,
                            executable_background_job_scope_filter(),
                        )
                    ),
                )
                .values(
                    status=JobStatus.processing,
                    locked_at=now,
                    locked_by=lease_token,
                    updated_at=now,
                )
            )
            if result.rowcount == 1:
                claimed_ids.append(job_id)
        if not claimed_ids:
            db.rollback()
            return []
        db.commit()

    return (
        db.query(BackgroundJob)
        .filter(BackgroundJob.id.in_(claimed_ids))
        .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
        .all()
    )


def _scope_signature(values) -> tuple[object, ...]:
    return (
        values.scope_type,
        values.tenant_id,
        values.customer_id,
        values.purpose,
        values.resource_type,
        values.resource_id,
    )


def require_executable_background_job_scope(
    db: Session,
    job: BackgroundJob,
) -> BackgroundJobScope:
    """Revalidate immutable ownership and purpose immediately before execution."""

    scope = db.get(BackgroundJobScope, int(job.id))
    if scope is None:
        raise BackgroundJobExecutionScopeError("background_job_scope_missing")
    if scope.source_schema != SCOPE_SCHEMA:
        raise BackgroundJobExecutionScopeError("background_job_scope_schema_stale")

    expected_purpose = PURPOSE_BY_JOB_TYPE.get(job.job_type)
    if expected_purpose is None or scope.purpose != expected_purpose:
        raise BackgroundJobExecutionScopeError("background_job_purpose_mismatch")
    if scope.scope_type == "platform":
        if job.job_type not in PLATFORM_JOB_TYPES or scope.tenant_id is not None:
            raise BackgroundJobExecutionScopeError(
                "background_job_platform_scope_not_authorized"
            )
    elif scope.scope_type == "tenant":
        if scope.tenant_id is None:
            raise BackgroundJobExecutionScopeError(
                "background_job_tenant_scope_missing"
            )
    else:
        raise BackgroundJobExecutionScopeError("background_job_scope_unresolved")

    current = derive_job_scope_values(db.connection(), job)
    stored_signature = (
        scope.scope_type,
        scope.tenant_id,
        scope.customer_id,
        scope.purpose,
        scope.resource_type,
        scope.resource_id,
    )
    if stored_signature != _scope_signature(current):
        raise BackgroundJobExecutionScopeError("background_job_scope_drift")

    if scope.scope_type == "tenant" and scope.customer_id is not None:
        ensure_data_processing_allowed(
            db,
            customer_id=scope.customer_id,
            purpose=scope.purpose,
        )
    return scope


__all__ = [
    "BackgroundJobExecutionScopeError",
    "claim_executable_background_jobs",
    "executable_background_job_scope_filter",
    "require_executable_background_job_scope",
]
