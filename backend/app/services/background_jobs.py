from __future__ import annotations

import json
import uuid
from datetime import timedelta
from time import perf_counter

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..enums import JobStatus, MessageStatus, SourceChannel
from ..models import BackgroundJob, OpenClawConversationLink, OpenClawTranscriptMessage, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from . import openclaw_bridge, openclaw_client_factory
from .observability import record_worker_job_metric

settings = get_settings()
AUTO_REPLY_JOB = 'auto_reply.send_update'
OPENCLAW_SYNC_JOB = 'openclaw.sync_session'
ATTACHMENT_PERSIST_JOB = 'openclaw.persist_attachment'
WEBCHAT_AI_REPLY_JOB = 'webchat.ai_reply'


def enqueue_background_job(
    db: Session,
    *,
    queue_name: str,
    job_type: str,
    payload: dict,
    max_attempts: int | None = None,
    next_run_at=None,
    dedupe_key: str | None = None,
) -> BackgroundJob:
    if dedupe_key:
        existing = (
            db.query(BackgroundJob)
            .filter(BackgroundJob.dedupe_key == dedupe_key, BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]))
            .order_by(BackgroundJob.id.desc())
            .first()
        )
        if existing is not None:
            return existing
    job = BackgroundJob(
        queue_name=queue_name,
        job_type=job_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
        dedupe_key=dedupe_key,
        status=JobStatus.pending,
        max_attempts=max_attempts or settings.job_max_retries,
        next_run_at=next_run_at,
    )
    db.add(job)
    db.flush()
    return job


def enqueue_auto_reply_job(db: Session, *, ticket_id: int, user_id: int) -> BackgroundJob:
    return enqueue_background_job(
        db,
        queue_name='auto_reply',
        job_type=AUTO_REPLY_JOB,
        payload={'ticket_id': ticket_id, 'user_id': user_id},
        dedupe_key=f'auto-reply:{ticket_id}',
    )


def enqueue_openclaw_sync_job(db: Session, *, ticket_id: int, session_key: str, transcript_limit: int | None = None, dedupe: bool = True) -> BackgroundJob:
    payload = {'ticket_id': ticket_id, 'session_key': session_key, 'transcript_limit': transcript_limit or settings.openclaw_sync_transcript_limit}
    return enqueue_background_job(db, queue_name='openclaw_sync', job_type=OPENCLAW_SYNC_JOB, payload=payload, dedupe_key=f'openclaw-sync:{session_key}' if dedupe else None)


def enqueue_attachment_persist_job(db: Session, *, attachment_ref_id: int, dedupe: bool = True) -> BackgroundJob:
    return enqueue_background_job(db, queue_name='openclaw_attachment', job_type=ATTACHMENT_PERSIST_JOB, payload={'attachment_ref_id': attachment_ref_id}, dedupe_key=f'openclaw-attachment:{attachment_ref_id}' if dedupe else None)


def enqueue_webchat_ai_reply_job(db: Session, *, conversation_id: int, ticket_id: int, visitor_message_id: int) -> BackgroundJob:
    return enqueue_background_job(
        db,
        queue_name='webchat_ai_reply',
        job_type=WEBCHAT_AI_REPLY_JOB,
        payload={
            'conversation_id': conversation_id,
            'ticket_id': ticket_id,
            'visitor_message_id': visitor_message_id,
        },
        dedupe_key=f'webchat-ai-reply:{visitor_message_id}',
    )


def enqueue_stale_openclaw_sync_jobs(db: Session, *, limit: int | None = None) -> list[BackgroundJob]:
    if not settings.openclaw_sync_enabled:
        return []
    cutoff = utc_now() - timedelta(seconds=settings.openclaw_sync_stale_seconds)
    rows = (
        db.query(OpenClawConversationLink)
        .join(OpenClawConversationLink.ticket)
        .filter(OpenClawConversationLink.session_key.is_not(None))
        .filter((OpenClawConversationLink.last_synced_at.is_(None)) | (OpenClawConversationLink.last_synced_at < cutoff))
        .order_by(OpenClawConversationLink.last_synced_at.asc().nullsfirst(), OpenClawConversationLink.id.asc())
        .limit(limit or settings.openclaw_sync_batch_size)
        .all()
    )
    jobs: list[BackgroundJob] = []
    seen: set[str] = set()
    for row in rows:
        if not row.session_key or row.session_key in seen:
            continue
        seen.add(row.session_key)
        jobs.append(enqueue_openclaw_sync_job(db, ticket_id=row.ticket_id, session_key=row.session_key, transcript_limit=settings.openclaw_sync_transcript_limit, dedupe=True))
    return jobs


def claim_pending_jobs(db: Session, *, limit: int | None = None, worker_id: str | None = None, job_types: list[str] | tuple[str, ...] | set[str] | None = None) -> list[BackgroundJob]:
    worker_id = worker_id or f'job-worker-{uuid.uuid4().hex[:8]}'
    limit = limit or settings.job_batch_size
    now = utc_now()
    lock_deadline = now - timedelta(seconds=settings.job_lock_seconds)
    normalized_job_types = tuple(sorted({str(job_type) for job_type in (job_types or []) if job_type}))
    pending_filters = [BackgroundJob.status == JobStatus.pending, or_(BackgroundJob.next_run_at.is_(None), BackgroundJob.next_run_at <= now), or_(BackgroundJob.locked_at.is_(None), BackgroundJob.locked_at < lock_deadline)]
    if normalized_job_types:
        pending_filters.append(BackgroundJob.job_type.in_(normalized_job_types))

    if db.bind and db.bind.dialect.name.startswith('postgresql'):
        rows = db.execute(select(BackgroundJob.id).where(*pending_filters).order_by(BackgroundJob.created_at.asc()).limit(limit).with_for_update(skip_locked=True)).all()
        claimed_ids = [row[0] for row in rows]
        if not claimed_ids:
            db.rollback()
            return []
        db.execute(update(BackgroundJob).where(BackgroundJob.id.in_(claimed_ids)).values(status=JobStatus.processing, locked_at=now, locked_by=worker_id))
        db.commit()
    else:
        candidate_ids = [row[0] for row in db.query(BackgroundJob.id).filter(*pending_filters).order_by(BackgroundJob.created_at.asc()).limit(limit).all()]
        claimed_ids: list[int] = []
        for job_id in candidate_ids:
            updated = db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id, *pending_filters).values(status=JobStatus.processing, locked_at=now, locked_by=worker_id))
            if updated.rowcount == 1:
                claimed_ids.append(job_id)
        if not claimed_ids:
            db.rollback()
            return []
        db.commit()
    return db.query(BackgroundJob).filter(BackgroundJob.id.in_(claimed_ids)).order_by(BackgroundJob.created_at.asc()).all()


def _mark_done(job: BackgroundJob) -> None:
    job.status = JobStatus.done
    job.locked_at = None
    job.locked_by = None
    job.next_run_at = None
    job.last_error = None
    job.updated_at = utc_now()


def _mark_retry(job: BackgroundJob, reason: str) -> None:
    job.attempt_count += 1
    job.last_error = reason[:500]
    job.locked_at = None
    job.locked_by = None
    backoff_minutes = min(2 ** max(job.attempt_count - 1, 0), 30)
    if job.attempt_count >= job.max_attempts:
        job.status = JobStatus.dead
        job.next_run_at = None
    else:
        job.status = JobStatus.pending
        job.next_run_at = utc_now() + timedelta(minutes=backoff_minutes)
    job.updated_at = utc_now()


def _draft_ai_auto_reply(db: Session, *, ticket, user, body: str, channel: SourceChannel) -> TicketOutboundMessage:
    message = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=channel,
        status=MessageStatus.draft,
        body=body,
        provider_status='ai_review_required',
        error_message='AI-generated auto reply saved as draft by outbound safety gate',
        failure_code='ai_review_required',
        failure_reason='AI-generated outbound requires human review before direct send',
        created_by=user.id,
        max_retries=settings.outbox_max_retries,
    )
    db.add(message)
    db.flush()
    return message


def process_background_job(db: Session, job: BackgroundJob) -> BackgroundJob:
    payload = json.loads(job.payload_json or '{}')
    try:
        if job.job_type == AUTO_REPLY_JOB:
            from .ticket_service import get_ticket_or_404, get_user_or_404
            from .llm_service import polish_reply_text
            from .bulletin_service import build_bulletin_context

            ticket = get_ticket_or_404(db, int(payload['ticket_id']))
            user = get_user_or_404(db, int(payload['user_id']))
            if not ticket.preferred_reply_contact and ticket.openclaw_link is None:
                _mark_done(job)
                return job
            human_note = ticket.customer_update or ticket.resolution_summary or ticket.last_human_update
            if not human_note:
                _mark_done(job)
                return job
            transcript_rows = db.query(OpenClawTranscriptMessage).filter(OpenClawTranscriptMessage.ticket_id == ticket.id).order_by(OpenClawTranscriptMessage.created_at.desc()).limit(5).all()
            transcript_context = '\n'.join(reversed([row.body_text for row in transcript_rows if row.body_text]))
            customer_request = transcript_context or ticket.customer_request or ticket.description or ''
            bulletin_context = build_bulletin_context(db, ticket=ticket)
            polished_text = polish_reply_text(customer_request, human_note, bulletin_context=bulletin_context)
            channel_value = (ticket.preferred_reply_channel or 'whatsapp').lower().strip()
            try:
                channel = SourceChannel(channel_value)
            except Exception:
                channel = SourceChannel.whatsapp
            _draft_ai_auto_reply(db, ticket=ticket, user=user, body=polished_text, channel=channel)
            _mark_done(job)
            return job

        if job.job_type == ATTACHMENT_PERSIST_JOB:
            from ..models import OpenClawAttachmentReference

            row = db.query(OpenClawAttachmentReference).filter(OpenClawAttachmentReference.id == int(payload['attachment_ref_id'])).first()
            if row is None:
                _mark_done(job)
                return job
            openclaw_bridge.persist_openclaw_attachment_reference(db, attachment_ref=row)
            row.updated_at = utc_now()
            _mark_done(job)
            return job

        if job.job_type == WEBCHAT_AI_REPLY_JOB:
            from .webchat_ai_safe_service import process_webchat_ai_reply_job

            process_webchat_ai_reply_job(
                db,
                conversation_id=int(payload['conversation_id']),
                ticket_id=int(payload['ticket_id']),
                visitor_message_id=int(payload['visitor_message_id']),
            )
            _mark_done(job)
            return job

        if job.job_type == OPENCLAW_SYNC_JOB:
            if not settings.openclaw_sync_enabled:
                _mark_done(job)
                return job
            with openclaw_client_factory.get_openclaw_runtime_client() as client:
                openclaw_bridge.sync_openclaw_conversation(
                    db,
                    ticket_id=int(payload['ticket_id']),
                    session_key=str(payload['session_key']),
                    limit=int(payload.get('transcript_limit') or settings.openclaw_sync_transcript_limit),
                    client=client,
                )
            _mark_done(job)
            return job

        raise RuntimeError(f'Unsupported job type: {job.job_type}')
    except Exception as exc:
        _mark_retry(job, str(exc))
        return job


def _job_wait_ms(job: BackgroundJob) -> float | None:
    if not job.created_at:
        return None
    try:
        return max((utc_now() - job.created_at).total_seconds() * 1000.0, 0.0)
    except Exception:
        return None


def _process_and_record_job(db: Session, job: BackgroundJob) -> BackgroundJob:
    started = perf_counter()
    wait_ms = _job_wait_ms(job)
    before_attempts = job.attempt_count or 0
    result = 'unknown'
    try:
        processed = process_background_job(db, job)
        if processed.status == JobStatus.done:
            result = 'success'
        elif processed.status == JobStatus.pending:
            result = 'retry'
        elif processed.status == JobStatus.dead:
            result = 'failed'
        else:
            result = str(processed.status.value if hasattr(processed.status, 'value') else processed.status)
        return processed
    finally:
        duration_ms = (perf_counter() - started) * 1000.0
        retry_delta = max((job.attempt_count or 0) - before_attempts, 0)
        record_worker_job_metric(job.job_type, result, duration_ms=duration_ms, wait_ms=wait_ms, retry_count=retry_delta)


def dispatch_pending_background_jobs(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[BackgroundJob]:
    if settings.openclaw_sync_enabled:
        enqueue_stale_openclaw_sync_jobs(db, limit=settings.openclaw_sync_batch_size)
        db.commit()
    claimed = claim_pending_jobs(db, limit=limit, worker_id=worker_id, job_types=[AUTO_REPLY_JOB, ATTACHMENT_PERSIST_JOB, WEBCHAT_AI_REPLY_JOB])
    processed: list[BackgroundJob] = []
    for job in claimed:
        _process_and_record_job(db, job)
        processed.append(job)
    db.commit()
    return processed


def dispatch_pending_sync_jobs(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[BackgroundJob]:
    if settings.openclaw_sync_enabled:
        enqueue_stale_openclaw_sync_jobs(db, limit=settings.openclaw_sync_batch_size)
        db.commit()
    claimed = claim_pending_jobs(db, limit=limit, worker_id=worker_id, job_types=[OPENCLAW_SYNC_JOB])
    processed: list[BackgroundJob] = []
    for job in claimed:
        if job.job_type != OPENCLAW_SYNC_JOB:
            continue
        _process_and_record_job(db, job)
        processed.append(job)
    db.commit()
    return processed
