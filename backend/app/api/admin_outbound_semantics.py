from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import JobStatus
from ..models import BackgroundJob, ExternalChannelConversationLink, ExternalChannelTranscriptMessage, ExternalChannelUnresolvedEvent
from ..settings import get_settings
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
        'external_channel_links': db.query(ExternalChannelConversationLink).count(),
        'external_channel_transcript_messages': db.query(ExternalChannelTranscriptMessage).count(),
        'external_channel_unresolved_events': db.query(ExternalChannelUnresolvedEvent).count(),
    }


@router.get('/external_channel/runtime-health')
def external_channel_runtime_health_with_outbound_semantics(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Compatibility endpoint exposing historical counts without runtime controls."""

    ensure_can_manage_runtime(current_user, db)
    outbound_counts = _outbound_counts(db)
    webchat_counts = _webchat_local_counts(outbound_counts)
    legacy_job_types = ("external_channel.sync_session", "external_channel.persist_attachment")
    legacy_pending_jobs = db.query(BackgroundJob).filter(
        BackgroundJob.job_type.in_(legacy_job_types),
        BackgroundJob.status == JobStatus.pending,
    ).count()
    legacy_dead_jobs = db.query(BackgroundJob).filter(
        BackgroundJob.job_type.in_(legacy_job_types),
        BackgroundJob.status == JobStatus.dead,
    ).count()
    warnings = ["ExternalChannel runtime is retired and historical state is read-only"]
    if legacy_pending_jobs or legacy_dead_jobs:
        warnings.append("Historical legacy jobs require offline migration; workers will not execute them")
    if outbound_counts['external_pending_outbound'] > 0 and not settings.enable_outbound_dispatch:
        warnings.append('External outbound messages are pending while outbound dispatch is disabled')
    return {
        'status': 'retired_read_only',
        'sync_cursor': None,
        'sync_daemon_last_seen_at': None,
        'sync_daemon_status': 'retired',
        'stale_link_count': 0,
        'external_channel_links_count': db.query(ExternalChannelConversationLink).count(),
        'transcript_messages_count': db.query(ExternalChannelTranscriptMessage).count(),
        'unresolved_events_count': db.query(ExternalChannelUnresolvedEvent).count(),
        'pending_sync_jobs': 0,
        'dead_sync_jobs': 0,
        'pending_attachment_jobs': 0,
        'dead_attachment_jobs': 0,
        'historical_pending_jobs': legacy_pending_jobs,
        'historical_dead_jobs': legacy_dead_jobs,
        'external_pending_outbound': outbound_counts['external_pending_outbound'],
        'external_dead_outbound': outbound_counts['external_dead_outbound'],
        **webchat_counts,
        'outbound_dispatch_enabled': bool(settings.enable_outbound_dispatch),
        'outbound_provider': settings.outbound_provider,
        'external_channel_bridge_allow_writes': False,
        'external_channel_cli_fallback_enabled': False,
        'warnings': warnings,
    }
