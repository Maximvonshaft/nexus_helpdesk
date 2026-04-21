from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db, engine
from ..enums import JobStatus, MessageStatus
from ..models import AIConfigResource, BackgroundJob, ChannelAccount, IntegrationClient, Market, MarketBulletin, OpenClawAttachmentReference, OpenClawConversationLink, OpenClawSyncCursor, ServiceHeartbeat, Team, TicketOutboundMessage, User, UserCapabilityOverride
from ..schemas import UserUpdate, PasswordResetRequest, OpenClawUnresolvedEventRead, AIConfigPublishRequest, AIConfigResourceCreate, AIConfigResourceRead, AIConfigResourceUpdate, AIConfigVersionRead, BackgroundJobRead, CapabilityOverrideRead, CapabilityOverrideUpsertRequest, ChannelAccountCreate, ChannelAccountRead, ChannelAccountUpdate, IntegrationClientRead, MarketBulletinCreate, MarketBulletinRead, MarketBulletinUpdate, MarketCreate, MarketRead, OpenClawConnectivityProbeRead, OpenClawConversationRead, OpenClawLinkRequest, OpenClawRuntimeHealthRead, OpenClawSyncEnqueueRequest, OpenClawSyncResult, ProductionReadinessRead, QueueSummaryRead, TeamMarketAssignRequest, TeamRead, UserCapabilityMatrixRead, UserRead, UserCreate
from ..settings import get_settings
from ..auth_service import hash_password
from ..utils.time import utc_now
from ..services.permissions import (
    ALL_CAPABILITIES,
    ensure_can_manage_capabilities,
    ensure_can_manage_users,
    ensure_can_manage_channel_accounts,
    ensure_can_manage_bulletins,
    ensure_can_manage_ai_configs,
    ensure_can_manage_runtime,
    ensure_can_manage_markets,
    resolve_capabilities,
    _base_capabilities,
)
from ..services.audit_service import log_admin_audit
from ..services.ai_config_service import create_resource as create_ai_config_resource, list_admin_resources, list_versions as list_ai_config_versions, publish_resource, rollback_resource, update_resource as update_ai_config_resource
from ..services.background_jobs import enqueue_openclaw_sync_job, enqueue_stale_openclaw_sync_jobs
from ..unit_of_work import managed_session
from ..services.openclaw_bridge import consume_openclaw_events_once, count_stale_openclaw_links, link_ticket_to_openclaw_session, list_stale_openclaw_links, sync_openclaw_conversation
from ..services.openclaw_runtime_service import probe_openclaw_connectivity
from .deps import get_current_user

settings = get_settings()

router = APIRouter(prefix='/api/admin', tags=['admin'])

def _normalize_username(value: str) -> str:
    return value.strip()

def _normalize_display_name(value: str) -> str:
    return value.strip()

def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None

def _is_last_active_admin(db: Session, user_id: int) -> bool:
    from ..models import User, UserRole
    return db.query(User).filter(User.role == UserRole.admin, User.is_active.is_(True)).count() == 1 and db.query(User).filter(User.id == user_id, User.role == UserRole.admin, User.is_active.is_(True)).first() is not None



@router.get('/capabilities/catalog', response_model=list[str])
def list_capability_catalog(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_can_manage_capabilities(current_user, db)
    return sorted(ALL_CAPABILITIES)


@router.get('/users/{user_id}/capabilities', response_model=UserCapabilityMatrixRead)
def get_user_capabilities(user_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    overrides = db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user.id).order_by(UserCapabilityOverride.capability.asc()).all()
    return UserCapabilityMatrixRead(
        user=UserRead.model_validate(user),
        effective_capabilities=sorted(resolve_capabilities(user, db)),
        overrides=[CapabilityOverrideRead.model_validate(item) for item in overrides],
    )


@router.put('/users/{user_id}/capabilities/{capability}', response_model=CapabilityOverrideRead)
def upsert_user_capability(user_id: int, capability: str, payload: CapabilityOverrideUpsertRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    if capability != payload.capability:
        raise HTTPException(status_code=400, detail='Capability path and body mismatch')
    if capability not in ALL_CAPABILITIES:
        raise HTTPException(status_code=400, detail='Unknown capability')
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    row = db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user_id, UserCapabilityOverride.capability == capability).first()
    with managed_session(db):
        if row is None:
            row = UserCapabilityOverride(user_id=user_id, capability=capability, allowed=payload.allowed)
            db.add(row)
            db.flush()
        else:
            row.allowed = payload.allowed
            db.flush()
    db.refresh(row)
    return CapabilityOverrideRead.model_validate(row)


@router.delete('/users/{user_id}/capabilities/{capability}')
def delete_user_capability(user_id: int, capability: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user_id, UserCapabilityOverride.capability == capability).first()
    if not row:
        raise HTTPException(status_code=404, detail='Capability override not found')
    with managed_session(db):
        db.delete(row)
    return {'ok': True}


@router.get('/integration-clients', response_model=list[IntegrationClientRead])
def list_integration_clients(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    rows = db.query(IntegrationClient).order_by(IntegrationClient.name.asc()).all()
    return [IntegrationClientRead.model_validate(x) for x in rows]


@router.get('/markets', response_model=list[MarketRead])
def list_markets(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    rows = db.query(Market).order_by(Market.country_code.asc(), Market.name.asc()).all()
    return [MarketRead.model_validate(x) for x in rows]


@router.post('/markets', response_model=MarketRead)
def create_market(payload: MarketCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = Market(
        code=payload.code.upper(),
        name=payload.name,
        country_code=payload.country_code.upper(),
        language_code=payload.language_code,
        timezone=payload.timezone,
    )
    with managed_session(db):
        db.add(row)
        db.flush()
    db.refresh(row)
    return MarketRead.model_validate(row)


@router.put('/teams/{team_id}/market', response_model=TeamRead)
def assign_team_market(team_id: int, payload: TeamMarketAssignRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    team = db.query(Team).filter(Team.id == team_id, Team.is_active.is_(True)).first()
    if not team:
        raise HTTPException(status_code=404, detail='Team not found')
    if payload.market_id is not None:
        market = db.query(Market).filter(Market.id == payload.market_id, Market.is_active.is_(True)).first()
        if not market:
            raise HTTPException(status_code=404, detail='Market not found')
    with managed_session(db):
        team.market_id = payload.market_id
        db.flush()
    db.refresh(team)
    return TeamRead.model_validate(team)


@router.post('/openclaw/link', response_model=OpenClawConversationRead)
def link_openclaw_ticket(payload: OpenClawLinkRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = link_ticket_to_openclaw_session(
            db,
            ticket_id=payload.ticket_id,
            session_key=payload.session_key,
            channel=payload.channel,
            recipient=payload.recipient,
            account_id=payload.account_id,
            thread_id=payload.thread_id,
            route=payload.route,
        )
    db.refresh(row)
    return OpenClawConversationRead.model_validate(row)


@router.post('/openclaw/tickets/{ticket_id}/sync', response_model=OpenClawSyncResult)
def sync_openclaw_ticket(ticket_id: int, session_key: str, limit: int = 50, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        result = sync_openclaw_conversation(db, ticket_id=ticket_id, session_key=session_key, limit=limit)
    return result


@router.get('/openclaw/links', response_model=list[OpenClawConversationRead])
def list_openclaw_links(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    rows = db.query(OpenClawConversationLink).order_by(OpenClawConversationLink.updated_at.desc()).all()
    return [OpenClawConversationRead.model_validate(x) for x in rows]


@router.post('/openclaw/sync/enqueue', response_model=BackgroundJobRead)
def enqueue_openclaw_sync(payload: OpenClawSyncEnqueueRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        job = enqueue_openclaw_sync_job(
            db,
            ticket_id=payload.ticket_id,
            session_key=payload.session_key,
            transcript_limit=payload.transcript_limit,
            dedupe=payload.dedupe,
        )
        db.flush()
    db.refresh(job)
    return BackgroundJobRead.model_validate(job)


@router.post('/openclaw/sync/enqueue-stale', response_model=list[BackgroundJobRead])
def enqueue_stale_openclaw_sync(limit: int = 25, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        rows = enqueue_stale_openclaw_sync_jobs(db, limit=limit)
        db.flush()
    for row in rows:
        db.refresh(row)
    return [BackgroundJobRead.model_validate(x) for x in rows]


@router.get('/queues/summary', response_model=QueueSummaryRead)
def get_queue_summary(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    return QueueSummaryRead(
        pending_outbound=db.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.pending).count(),
        dead_outbound=db.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.dead).count(),
        pending_jobs=db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.pending).count(),
        dead_jobs=db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.dead).count(),
        openclaw_links=db.query(OpenClawConversationLink).count(),
    )


@router.get('/jobs', response_model=list[BackgroundJobRead])
def list_background_jobs(status: str | None = None, limit: int = 100, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    query = db.query(BackgroundJob).order_by(BackgroundJob.created_at.desc())
    if status:
        query = query.filter(BackgroundJob.status == status)
    rows = query.limit(limit).all()
    return [BackgroundJobRead.model_validate(x) for x in rows]


@router.get('/production-readiness', response_model=ProductionReadinessRead)
def production_readiness(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    warnings: list[str] = []
    if not settings.is_postgres:
        warnings.append('DATABASE_URL is not PostgreSQL; stage/prod cutover is still pending')
    if settings.storage_backend == 'local':
        warnings.append('STORAGE_BACKEND=local; object storage cutover is still pending')
    if settings.openclaw_transport != 'mcp':
        warnings.append('OpenClaw transport is not MCP-first')
    if not settings.metrics_enabled:
        warnings.append('Metrics are disabled')
    if db.bind and not db.bind.dialect.name.startswith('postgresql'):
        warnings.append('Current runtime DB dialect is not PostgreSQL')
    return ProductionReadinessRead(
        app_env=settings.app_env,
        database_url_scheme=settings.database_url.split(':', 1)[0],
        is_postgres=settings.is_postgres,
        storage_backend=settings.storage_backend,
        openclaw_transport=settings.openclaw_transport,
        metrics_enabled=settings.metrics_enabled,
        openclaw_sync_enabled=settings.openclaw_sync_enabled,
        warnings=warnings,
    )


@router.get('/signoff-checklist')
def signoff_checklist(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    checks['postgres_configured'] = settings.is_postgres
    if not checks['postgres_configured']:
        warnings.append('DATABASE_URL is not PostgreSQL')

    checks['storage_not_local'] = settings.storage_backend != 'local'
    if not checks['storage_not_local']:
        warnings.append('STORAGE_BACKEND is local')

    checks['openclaw_transport_mcp'] = settings.openclaw_transport == 'mcp'
    if not checks['openclaw_transport_mcp']:
        warnings.append('OPENCLAW_TRANSPORT is not mcp')

    checks['metrics_enabled'] = settings.metrics_enabled
    if not checks['metrics_enabled']:
        warnings.append('METRICS_ENABLED is false')

    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        checks['database_connected'] = True
    except Exception:
        checks['database_connected'] = False
        warnings.append('Database connectivity check failed')

    if settings.openclaw_event_driver_enabled is False:
        warnings.append('OPENCLAW_EVENT_DRIVER_ENABLED is false')
    return {
        'status': 'ready' if not warnings else 'not_ready',
        'checks': checks,
        'warnings': warnings,
    }


@router.get('/channel-accounts', response_model=list[ChannelAccountRead])
def list_channel_accounts(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    rows = db.query(ChannelAccount).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).all()
    return [ChannelAccountRead.model_validate(x) for x in rows]


@router.post('/channel-accounts', response_model=ChannelAccountRead)
def create_channel_account(payload: ChannelAccountCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = ChannelAccount(
            provider=payload.provider,
            account_id=payload.account_id,
            display_name=payload.display_name,
            market_id=payload.market_id,
            priority=payload.priority,
            fallback_account_id=payload.fallback_account_id,
            health_status='unknown',
        )
        db.add(row)
        db.flush()
    db.refresh(row)
    return ChannelAccountRead.model_validate(row)


@router.patch('/channel-accounts/{account_id}', response_model=ChannelAccountRead)
def update_channel_account(account_id: int, payload: ChannelAccountUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(ChannelAccount).filter(ChannelAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='Channel account not found')
    with managed_session(db):
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(row, key, value)
        db.flush()
    db.refresh(row)
    return ChannelAccountRead.model_validate(row)


@router.get('/openclaw/runtime-health', response_model=OpenClawRuntimeHealthRead)
def openclaw_runtime_health(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    cursor = db.query(OpenClawSyncCursor).filter(OpenClawSyncCursor.source == 'default').first()
    heartbeat = db.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == 'openclaw_event_daemon').first()
    stale_link_count = count_stale_openclaw_links(db)
    pending_sync_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.sync_session', BackgroundJob.status == JobStatus.pending).count()
    dead_sync_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.sync_session', BackgroundJob.status == JobStatus.dead).count()
    pending_attachment_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.persist_attachment', BackgroundJob.status == JobStatus.pending).count()
    dead_attachment_jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == 'openclaw.persist_attachment', BackgroundJob.status == JobStatus.dead).count()
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
    return OpenClawRuntimeHealthRead(
        sync_cursor=cursor.cursor_value if cursor else None,
        sync_daemon_last_seen_at=daemon_seen,
        sync_daemon_status=daemon_status,
        stale_link_count=stale_link_count,
        pending_sync_jobs=pending_sync_jobs,
        dead_sync_jobs=dead_sync_jobs,
        pending_attachment_jobs=pending_attachment_jobs,
        dead_attachment_jobs=dead_attachment_jobs,
        warnings=warnings,
    )


@router.get('/openclaw/connectivity-check', response_model=OpenClawConnectivityProbeRead)
def openclaw_connectivity_check(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    return probe_openclaw_connectivity()


@router.post('/openclaw/events/consume-once')
def consume_openclaw_events(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        processed = consume_openclaw_events_once(db)
        db.flush()
    return {'processed': processed}




@router.get('/ai-configs', response_model=list[AIConfigResourceRead])
def list_ai_configs(config_type: str | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    rows = list_admin_resources(db, config_type=config_type)
    return [AIConfigResourceRead.model_validate(row) for row in rows]


@router.post('/ai-configs', response_model=AIConfigResourceRead)
def create_ai_config(payload: AIConfigResourceCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = create_ai_config_resource(db, payload, current_user)
    db.refresh(row)
    return AIConfigResourceRead.model_validate(row)


@router.patch('/ai-configs/{resource_id}', response_model=AIConfigResourceRead)
def update_ai_config(resource_id: int, payload: AIConfigResourceUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    with managed_session(db):
        row = update_ai_config_resource(db, row, payload, current_user)
    db.refresh(row)
    return AIConfigResourceRead.model_validate(row)


@router.post('/ai-configs/{resource_id}/publish', response_model=AIConfigVersionRead)
def publish_ai_config(resource_id: int, payload: AIConfigPublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    with managed_session(db):
        version_row = publish_resource(db, row, current_user, notes=payload.notes)
    db.refresh(version_row)
    return AIConfigVersionRead.model_validate(version_row)


@router.get('/ai-configs/{resource_id}/versions', response_model=list[AIConfigVersionRead])
def get_ai_config_versions(resource_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    return [AIConfigVersionRead.model_validate(item) for item in list_ai_config_versions(db, resource_id)]


@router.post('/ai-configs/{resource_id}/rollback/{version}', response_model=AIConfigVersionRead)
def rollback_ai_config(resource_id: int, version: int, payload: AIConfigPublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    with managed_session(db):
        version_row = rollback_resource(db, row, version, current_user, notes=payload.notes)
    db.refresh(version_row)
    return AIConfigVersionRead.model_validate(version_row)

@router.get('/bulletins', response_model=list[MarketBulletinRead])
def list_bulletins(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    rows = db.query(MarketBulletin).order_by(MarketBulletin.updated_at.desc()).all()
    return [MarketBulletinRead.model_validate(x) for x in rows]


@router.post('/bulletins', response_model=MarketBulletinRead)
def create_bulletin(payload: MarketBulletinCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = MarketBulletin(
            market_id=payload.market_id,
            country_code=payload.country_code.upper() if payload.country_code else None,
            title=payload.title,
            body=payload.body,
            summary=payload.summary,
            category=payload.category,
            channels_csv=payload.channels_csv,
            audience=payload.audience,
            severity=payload.severity,
            auto_inject_to_ai=payload.auto_inject_to_ai,
            is_active=payload.is_active,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            created_by=current_user.id,
        )
        db.add(row)
        db.flush()
    db.refresh(row)
    return MarketBulletinRead.model_validate(row)


@router.patch('/bulletins/{bulletin_id}', response_model=MarketBulletinRead)
def update_bulletin(bulletin_id: int, payload: MarketBulletinUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    row = db.query(MarketBulletin).filter(MarketBulletin.id == bulletin_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='Bulletin not found')
    with managed_session(db):
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key == 'country_code' and value:
                value = value.upper()
            setattr(row, key, value)
        db.flush()
    db.refresh(row)
    return MarketBulletinRead.model_validate(row)


@router.post('/users', response_model=UserRead)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    
    # Check if username exists
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
        
    if payload.email:
        existing_email = db.query(User).filter(User.email == payload.email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="Email already exists")

    hashed = hash_password(payload.password)
    
    new_user = User(
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        password_hash=hashed,
        role=payload.role,
        team_id=payload.team_id,
        is_active=True
    )
    
    db.add(new_user)
    db.flush()
    
    # Process explicit capabilities (overrides against base role)
    if payload.capabilities is not None:
        base_caps = _base_capabilities(new_user.role)
        requested_caps = set(payload.capabilities)
        for cap in ALL_CAPABILITIES:
            if cap in requested_caps and cap not in base_caps:
                db.add(UserCapabilityOverride(user_id=new_user.id, capability=cap, allowed=True))
            elif cap not in requested_caps and cap in base_caps:
                db.add(UserCapabilityOverride(user_id=new_user.id, capability=cap, allowed=False))

    db.commit()
    db.refresh(new_user)
    
    return new_user


@router.get('/users', response_model=list[UserRead])
def list_admin_users(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    from ..models import User
    rows = db.query(User).order_by(User.is_active.desc(), User.role.asc(), User.username.asc()).all()
    result = []
    for row in rows:
        payload = UserRead.model_validate(row).model_copy(update={
            'is_active': row.is_active,
            'capabilities': sorted(resolve_capabilities(row, db)),
            'created_at': row.created_at,
            'updated_at': row.updated_at,
        })
        result.append(payload)
    return result

@router.patch('/users/{user_id}', response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    from ..models import User, UserCapabilityOverride, UserRole
    from ..schemas import UserUpdate, PasswordResetRequest, OpenClawUnresolvedEventRead, UserUpdate
    row = db.query(User).filter(User.id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='User not found')
    if row.id == current_user.id and payload.role is not None and payload.role not in {UserRole.admin, UserRole.manager}:
        raise HTTPException(status_code=400, detail='当前管理员不能把自己降权到无治理能力')
    if row.role == UserRole.admin and payload.role is not None and payload.role != UserRole.admin and _is_last_active_admin(db, row.id):
        raise HTTPException(status_code=400, detail='不能降权最后一个 active admin')
    old_value = {'display_name': row.display_name, 'email': row.email, 'role': row.role.value, 'team_id': row.team_id}
    with managed_session(db):
        if payload.display_name is not None:
            row.display_name = _normalize_display_name(payload.display_name)
        if payload.email is not None:
            normalized_email = _normalize_email(payload.email)
            duplicate = db.query(User).filter(User.id != row.id, User.email == normalized_email).first()
            if duplicate:
                raise HTTPException(status_code=400, detail='Email already exists')
            row.email = normalized_email
        if payload.role is not None:
            row.role = payload.role
        if payload.team_id is not None:
            row.team_id = payload.team_id
        if payload.capabilities is not None:
            db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == row.id).delete()
            base_caps = _base_capabilities(row.role)
            requested_caps = set(payload.capabilities)
            for cap in ALL_CAPABILITIES:
                if cap in requested_caps and cap not in base_caps:
                    db.add(UserCapabilityOverride(user_id=row.id, capability=cap, allowed=True))
                elif cap not in requested_caps and cap in base_caps:
                    db.add(UserCapabilityOverride(user_id=row.id, capability=cap, allowed=False))
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.update', target_type='user', target_id=row.id, old_value=old_value, new_value={'display_name': row.display_name, 'email': row.email, 'role': row.role.value, 'team_id': row.team_id})
    db.refresh(row)
    return UserRead.model_validate(row).model_copy(update={'is_active': row.is_active, 'capabilities': sorted(resolve_capabilities(row, db)), 'created_at': row.created_at, 'updated_at': row.updated_at})

@router.post('/users/{user_id}/activate', response_model=UserRead)
def activate_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    from ..models import User
    row = db.query(User).filter(User.id == user_id).first()
    if not row: raise HTTPException(404, "User not found")
    with managed_session(db):
        row.is_active = True
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.activate', target_type='user', target_id=row.id, old_value={'is_active': False}, new_value={'is_active': True})
    db.refresh(row)
    return UserRead.model_validate(row).model_copy(update={'is_active': row.is_active, 'capabilities': sorted(resolve_capabilities(row, db)), 'created_at': row.created_at, 'updated_at': row.updated_at})

@router.post('/users/{user_id}/deactivate', response_model=UserRead)
def deactivate_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    from ..models import User, UserRole
    row = db.query(User).filter(User.id == user_id).first()
    if not row: raise HTTPException(404, "User not found")
    if row.id == current_user.id: raise HTTPException(400, "Cannot deactivate self")
    if row.role == UserRole.admin and _is_last_active_admin(db, row.id): raise HTTPException(400, "Cannot deactivate last admin")
    with managed_session(db):
        row.is_active = False
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.deactivate', target_type='user', target_id=row.id, old_value={'is_active': True}, new_value={'is_active': False})
    db.refresh(row)
    return UserRead.model_validate(row).model_copy(update={'is_active': row.is_active, 'capabilities': sorted(resolve_capabilities(row, db)), 'created_at': row.created_at, 'updated_at': row.updated_at})

@router.post('/users/{user_id}/reset-password')
def reset_user_password(user_id: int, payload: PasswordResetRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    from ..models import User
    row = db.query(User).filter(User.id == user_id).first()
    if not row: raise HTTPException(404, "User not found")
    with managed_session(db):
        row.password_hash = hash_password(payload.password)
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.reset_password', target_type='user', target_id=row.id, old_value={}, new_value={})
    return {"ok": True}

@router.get('/openclaw/unresolved-events', response_model=list[OpenClawUnresolvedEventRead])
def list_unresolved_events(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    from ..models import OpenClawUnresolvedEvent
    rows = db.query(OpenClawUnresolvedEvent).order_by(OpenClawUnresolvedEvent.created_at.desc()).all()
    return [OpenClawUnresolvedEventRead.model_validate(x) for x in rows]

@router.post('/openclaw/unresolved-events/{event_id}/replay')
def replay_unresolved_event(event_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    from ..models import OpenClawUnresolvedEvent
    row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == event_id).first()
    if not row: raise HTTPException(404, "Event not found")
    with managed_session(db):
        row.replay_count += 1
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='unresolved_event.replay', target_type='unresolved_event', target_id=row.id, old_value={}, new_value={'replay_count': row.replay_count})
    return {"ok": True}

@router.post('/openclaw/unresolved-events/{event_id}/drop')
def drop_unresolved_event(event_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    from ..models import OpenClawUnresolvedEvent
    row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == event_id).first()
    if not row: raise HTTPException(404, "Event not found")
    with managed_session(db):
        row.status = 'dropped'
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='unresolved_event.drop', target_type='unresolved_event', target_id=row.id, old_value={}, new_value={'status': 'dropped'})
    return {"ok": True}
