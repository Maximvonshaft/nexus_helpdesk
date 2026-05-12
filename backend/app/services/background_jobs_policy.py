from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..enums import EventType, JobStatus, MessageStatus
from ..models import BackgroundJob, OpenClawTranscriptMessage, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from .audit_service import log_event
from .background_jobs import (
    ATTACHMENT_PERSIST_JOB,
    AUTO_REPLY_JOB,
    WEBCHAT_AI_REPLY_JOB,
    claim_pending_jobs,
    enqueue_stale_openclaw_sync_jobs,
    process_background_job as legacy_process_background_job,
)
from .reply_channel_policy import ReplyTargetError, resolve_ticket_reply_target
from .webchat_formal_policy import is_formal_resolution_context, webchat_frontline_ai_enabled

settings = get_settings()


def _mark_done(job: BackgroundJob) -> None:
    job.status = JobStatus.done
    job.locked_at = None
    job.locked_by = None
    job.next_run_at = None
    job.last_error = None
    job.updated_at = utc_now()


def _draft_ai_reply(db: Session, *, ticket, user, body: str, channel) -> TicketOutboundMessage:
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=channel,
        status=MessageStatus.draft,
        body=body,
        provider_status='ai_review_required',
        error_message='AI-generated reply saved as draft and requires human review',
        failure_code='ai_review_required',
        failure_reason='AI-generated outbound requires human approval before dispatch',
        created_by=user.id,
        max_retries=settings.outbox_max_retries,
    )
    db.add(row)
    db.flush()
    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=user.id,
        event_type=EventType.outbound_draft_saved,
        note='ai_draft_created',
        payload={'channel': channel.value, 'outbound_message_id': row.id, 'provider_status': row.provider_status, 'actor_id': user.id},
    )
    return row


def _process_auto_reply_job(db: Session, job: BackgroundJob, payload: dict) -> BackgroundJob:
    from .bulletin_service import build_bulletin_context
    from .llm_service import polish_reply_text
    from .ticket_service import get_ticket_or_404, get_user_or_404

    ticket = get_ticket_or_404(db, int(payload['ticket_id']))
    user = get_user_or_404(db, int(payload['user_id']))
    human_note = ticket.customer_update or ticket.resolution_summary or ticket.last_human_update
    if not human_note:
        _mark_done(job)
        return job
    try:
        reply_target = resolve_ticket_reply_target(ticket)
    except ReplyTargetError as exc:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.internal_note_added,
            note='ai_draft_skipped_missing_reply_target',
            payload={'reason': exc.code, 'job_id': job.id},
        )
        _mark_done(job)
        return job
    transcript_rows = (
        db.query(OpenClawTranscriptMessage)
        .filter(OpenClawTranscriptMessage.ticket_id == ticket.id)
        .order_by(OpenClawTranscriptMessage.created_at.desc())
        .limit(5)
        .all()
    )
    transcript_context = '\n'.join(reversed([row.body_text for row in transcript_rows if row.body_text]))
    customer_request = transcript_context or ticket.customer_request or ticket.description or ''
    polished_text = polish_reply_text(customer_request, human_note, bulletin_context=build_bulletin_context(db, ticket=ticket))
    _draft_ai_reply(db, ticket=ticket, user=user, body=polished_text, channel=reply_target.channel)
    _mark_done(job)
    return job


def _process_webchat_ai_reply_job(db: Session, job: BackgroundJob, payload: dict) -> BackgroundJob:
    if not webchat_frontline_ai_enabled():
        log_event(
            db,
            ticket_id=int(payload['ticket_id']),
            actor_id=None,
            event_type=EventType.internal_note_added,
            note='webchat_frontline_ai_disabled',
            payload={'job_id': job.id, 'visitor_message_id': payload.get('visitor_message_id')},
        )
        _mark_done(job)
        return job

    from .ticket_service import get_ticket_or_404

    ticket = get_ticket_or_404(db, int(payload['ticket_id']))
    if is_formal_resolution_context(ticket, source='webchat_ai_reply'):
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.internal_note_added,
            note='webchat_final_resolution_suppressed',
            payload={'job_id': job.id, 'visitor_message_id': payload.get('visitor_message_id'), 'formal_outbound_disabled': True},
        )
        _mark_done(job)
        return job

    return legacy_process_background_job(db, job)


def process_background_job(db: Session, job: BackgroundJob) -> BackgroundJob:
    payload = json.loads(job.payload_json or '{}')
    if job.job_type == AUTO_REPLY_JOB:
        return _process_auto_reply_job(db, job, payload)
    if job.job_type == WEBCHAT_AI_REPLY_JOB:
        return _process_webchat_ai_reply_job(db, job, payload)
    return legacy_process_background_job(db, job)


def dispatch_pending_background_jobs(db: Session, *, limit: int | None = None, worker_id: str | None = None) -> list[BackgroundJob]:
    if settings.openclaw_sync_enabled:
        enqueue_stale_openclaw_sync_jobs(db, limit=settings.openclaw_sync_batch_size)
        db.commit()
    claimed = claim_pending_jobs(db, limit=limit, worker_id=worker_id, job_types=[AUTO_REPLY_JOB, ATTACHMENT_PERSIST_JOB, WEBCHAT_AI_REPLY_JOB])
    processed: list[BackgroundJob] = []
    for job in claimed:
        process_background_job(db, job)
        processed.append(job)
    db.commit()
    return processed
