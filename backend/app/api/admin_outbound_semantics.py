from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import JobStatus
from ..models import BackgroundJob, OpenClawConversationLink, OpenClawSyncCursor, OpenClawTranscriptMessage, OpenClawUnresolvedEvent, ServiceHeartbeat
from ..settings import get_settings
from ..utils.time import utc_now
from ..services.openclaw_bridge import count_stale_openclaw_links
from ..services.outbound_semantics import count_outbound_semantics
from ..services.permissions import ensure_can_manage_runtime
from .deps import get_current_user

settings = get_settings()
router = APIRouter(prefix='/api/admin', tags=['admin-outbound-semantics'])


def _outbound_counts(db: Session) -> dict[str, int]:
    return count_outbound_semantics(db)


def _webchat_local_counts(outbound_counts: dict[str, int]) -> dict[str, int]:
    return {
        'webchat_local_ack_sent': outbound_counts['webchat_local_ack_sent'],
        'webchat_ai_delivered_sent': outbound_counts['webchat_ai_delivered_sent'],
        'webchat_ai_safe_fallback_sent': outbound_counts['webchat_ai_safe_fallback_sent'],
        'webchat_card_sent': outbound_counts['webchat_card_sent'],
        'webchat_handoff_ack_sent': outbound_counts['webchat_handoff_ack_sent'],
    }


@router.get('/queues/summary')
def get_semantic_queue_summary(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    outbound_counts = _outbound_counts(db)
    webchat_counts = _webchat_local_counts(outbound_counts)
    return {
        # Keep legacy field names but make their runtime meaning safe: these now represent external sends only.
        'pending_outbound': outbound_counts['external_pending_outbound'],
        'dead_outbound': outbound_counts['external_dead_outbound'],
        'external_pending_outbound': outbound_counts['external_pending_outbound'],
        'external_dead_outbound': outbound_counts['external_dead_outbound'],
        **webchat_counts,
        'pending_jobs': db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.pending).count(),
        'dead_jobs': db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.dead).count(),
        'openclaw_links': db.query(OpenClawConversationLink).count(),
        'openclaw_transcript_messages': db.query(OpenClawTranscriptMessage).count(),
        'openclaw_unresolved_events': db.query(OpenClawUnresolvedEvent).count(),
    }


@router.get('/openclaw/runtime-health')
def openclaw_runtime_health_with_outbound_semantics(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    cursor = db.query(OpenClawSyncCursor).filter(OpenClawSyncCursor.source == 'default').first()
    heartbeat = db.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == 'openclaw_event_daemon').first()
    stale_link_count = count_stale_openclaw_links(db)
    pending_sync_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.sync_session', BackgroundJob.status == JobStatus.pending).count()
    dead_sync_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.sync_session', BackgroundJob.status == JobStatus.dead).count()
    pending_attachment_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.persist_attachment', BackgroundJob.status == JobStatus.pending).count()
    dead_attachment_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.persist_attachment', BackgroundJob.status == JobStatus.dead).count()
    outbound_counts = _outbound_counts(db)
    webchat_counts = _webchat_local_counts(outbound_counts)

    warnings: list[str] = []
    if heartbeat is None:
        warnings.append('OpenClaw event daemon heartbeat missing')
        daemon_status = None
        daemon_seen = None
    else:
        daemon_status = heartbeat.status
        daemon_seen = heartbeat.last_seen_at
        from ..utils.time import ensure_utc
        if daemon_seen and (utc_now() - ensure_utc(daemon_seen)).total_seconds() > settings.openclaw_sync_daemon_stale_seconds:
            warnings.append('OpenClaw event daemon heartbeat is stale')
    if stale_link_count > settings.openclaw_sync_batch_size:
        warnings.append('OpenClaw stale link backlog exceeds one batch')
    if dead_sync_jobs > 0:
        warnings.append('There are dead OpenClaw sync jobs')
    if dead_attachment_jobs > 0:
        warnings.append('There are dead OpenClaw attachment persist jobs')
    if outbound_counts['external_pending_outbound'] > 0 and not settings.enable_outbound_dispatch:
        warnings.append('External outbound messages are pending while outbound dispatch is disabled')

    return {
        'sync_cursor': cursor.cursor_value if cursor else None,
        'sync_daemon_last_seen_at': daemon_seen,
        'sync_daemon_status': daemon_status,
        'stale_link_count': stale_link_count,
        'openclaw_links_count': db.query(OpenClawConversationLink).count(),
        'transcript_messages_count': db.query(OpenClawTranscriptMessage).count(),
        'unresolved_events_count': db.query(OpenClawUnresolvedEvent).count(),
        'pending_sync_jobs': pending_sync_jobs,
        'dead_sync_jobs': dead_sync_jobs,
        'pending_attachment_jobs': pending_attachment_jobs,
        'dead_attachment_jobs': dead_attachment_jobs,
        'external_pending_outbound': outbound_counts['external_pending_outbound'],
        'external_dead_outbound': outbound_counts['external_dead_outbound'],
        **webchat_counts,
        'outbound_dispatch_enabled': bool(settings.enable_outbound_dispatch),
        'outbound_provider': settings.outbound_provider,
        'openclaw_bridge_allow_writes': bool(settings.openclaw_bridge_allow_writes),
        'openclaw_cli_fallback_enabled': bool(settings.openclaw_cli_fallback_enabled),
        'warnings': warnings,
    }
