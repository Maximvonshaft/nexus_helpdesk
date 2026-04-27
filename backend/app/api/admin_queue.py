from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import JobStatus, MessageStatus
from ..models import BackgroundJob, TicketOutboundMessage
from ..services.audit_service import log_admin_audit
from ..services.message_dispatch import requeue_dead_outbound_message
from ..services.permissions import ensure_can_manage_runtime
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix='/api/admin', tags=['admin-queue'])


def _requeue_dead_job_row(job: BackgroundJob) -> BackgroundJob:
    if job.status != JobStatus.dead:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Only dead jobs can be requeued')
    job.status = JobStatus.pending
    job.attempt_count = 0
    job.locked_at = None
    job.locked_by = None
    job.last_error = None
    job.next_run_at = utc_now()
    job.updated_at = utc_now()
    return job


@router.post('/jobs/{job_id}/requeue')
def requeue_job(job_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Background job not found')
    with managed_session(db):
        old_value = {'status': job.status.value if hasattr(job.status, 'value') else str(job.status), 'attempt_count': job.attempt_count}
        _requeue_dead_job_row(job)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='background_job.requeue',
            target_type='background_job',
            target_id=job.id,
            old_value=old_value,
            new_value={'status': 'pending', 'attempt_count': 0},
        )
        db.flush()
    return {'ok': True, 'job_id': job.id, 'status': job.status.value if hasattr(job.status, 'value') else str(job.status)}


@router.post('/jobs/requeue-dead')
def requeue_dead_jobs(job_type: str | None = None, limit: int = 50, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    query = db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.dead)
    if job_type:
        query = query.filter(BackgroundJob.job_type == job_type)
    rows = query.order_by(BackgroundJob.updated_at.asc()).limit(max(1, min(limit, 200))).all()
    with managed_session(db):
        for job in rows:
            _requeue_dead_job_row(job)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='background_job.requeue_dead_batch',
            target_type='background_job',
            target_id=None,
            old_value={'job_type': job_type, 'count': len(rows)},
            new_value={'status': 'pending'},
        )
        db.flush()
    return {'ok': True, 'requeued': len(rows), 'job_type': job_type}


@router.post('/outbound/{message_id}/requeue')
def requeue_outbound(message_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        message = requeue_dead_outbound_message(db, message_id=message_id)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='outbound_message.requeue',
            target_type='ticket_outbound_message',
            target_id=message.id,
            old_value={'status': 'dead'},
            new_value={'status': 'pending', 'retry_count': 0},
        )
        db.flush()
    return {'ok': True, 'message_id': message.id, 'status': message.status.value if hasattr(message.status, 'value') else str(message.status)}


@router.post('/outbound/requeue-dead')
def requeue_dead_outbound(limit: int = 50, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    rows = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.dead).order_by(TicketOutboundMessage.updated_at.asc()).limit(max(1, min(limit, 200))).all()
    with managed_session(db):
        count = 0
        for row in rows:
            requeue_dead_outbound_message(db, message_id=row.id)
            count += 1
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='outbound_message.requeue_dead_batch',
            target_type='ticket_outbound_message',
            target_id=None,
            old_value={'count': len(rows)},
            new_value={'status': 'pending'},
        )
        db.flush()
    return {'ok': True, 'requeued': count}
