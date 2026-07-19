from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..db import get_db, engine
from ..enums import JobStatus, MessageStatus, UserRole
from ..models import AIConfigResource, AdminAuditLog, BackgroundJob, ChannelAccount, IntegrationClient, Market, MarketBulletin, ExternalChannelConversationLink, ExternalChannelTranscriptMessage, ExternalChannelUnresolvedEvent, OutboundEmailAccount, Team, TicketOutboundMessage, User, UserCapabilityOverride
from ..schemas import UserUpdate, PasswordResetRequest, ExternalChannelUnresolvedEventRead, AdminAuditLogRead, AIConfigPublishRequest, AIConfigResourceCreate, AIConfigResourceRead, AIConfigResourceUpdate, AIConfigVersionRead, BackgroundJobRead, CapabilityOverrideRead, CapabilityOverrideUpsertRequest, ChannelAccountCreate, ChannelAccountRead, ChannelAccountUpdate, IntegrationClientRead, MarketBulletinCreate, MarketBulletinImpactPreviewRead, MarketBulletinImpactPreviewRequest, MarketBulletinRead, MarketBulletinUpdate, MarketCreate, MarketRead, ExternalChannelConversationRead, ProductionReadinessRead, QueueSummaryRead, SecurityAuditRead, SecurityAuditSummaryRead, SecurityCapabilityUserRead, TeamMarketAssignRequest, TeamRead, UserCapabilityMatrixRead, UserRead, UserCreate
from ..settings import get_settings
from ..auth_service import hash_password
from ..utils.time import utc_now
from ..services.permissions import (
    ALL_CAPABILITIES,
    CAP_USER_MANAGE,
    ensure_can_manage_users,
    ensure_can_view_security_audit,
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
from ..services.bulletin_service import bulletin_audit_snapshot, build_bulletin_impact_preview, normalize_bulletin_country_code
from ..unit_of_work import managed_session
from ..services.outbound_channel_registry import EXTERNAL_READY_CANDIDATE_CHANNELS
from ..services.outbound_email_account_service import count_active_successful_tested_accounts
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy
from .deps import get_current_user
from .admin_outbound_email import router as outbound_email_router

settings = get_settings()

router = APIRouter(prefix='/api/admin', tags=['admin'])
router.include_router(outbound_email_router)

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
    return db.query(User).filter(User.role == UserRole.admin, User.is_active.is_(True)).count() == 1 and db.query(User).filter(User.id == user_id, User.role == UserRole.admin, User.is_active.is_(True)).first() is not None


def _validate_password_length(password: str) -> None:
    try:
        validate_admin_password_policy(password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _ensure_user_uniqueness(db: Session, *, username: str, email: str | None, exclude_user_id: int | None = None) -> None:
    username_query = db.query(User).filter(User.username == username)
    if exclude_user_id is not None:
        username_query = username_query.filter(User.id != exclude_user_id)
    if username_query.first() is not None:
        raise HTTPException(status_code=400, detail='Username already exists')

    if email is None:
        return
    email_query = db.query(User).filter(User.email == email)
    if exclude_user_id is not None:
        email_query = email_query.filter(User.id != exclude_user_id)
    if email_query.first() is not None:
        raise HTTPException(status_code=400, detail='Email already exists')


def _serialize_user(row: User, db: Session) -> UserRead:
    return UserRead.model_validate(row).model_copy(update={
        'is_active': row.is_active,
        'capabilities': sorted(resolve_capabilities(row, db)),
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    })


def _apply_user_capability_overrides(db: Session, *, user_id: int, role, requested_capabilities: list[str]) -> None:
    db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user_id).delete()
    base_caps = _base_capabilities(role)
    requested_caps = set(requested_capabilities)
    for cap in ALL_CAPABILITIES:
        if cap in requested_caps and cap not in base_caps:
            db.add(UserCapabilityOverride(user_id=user_id, capability=cap, allowed=True))
        elif cap not in requested_caps and cap in base_caps:
            db.add(UserCapabilityOverride(user_id=user_id, capability=cap, allowed=False))


def _validate_channel_account_payload(
    db: Session,
    *,
    provider: str,
    account_id: str,
    market_id: int | None,
    fallback_account_id: str | None,
    current_row: ChannelAccount | None = None,
) -> tuple[str, str, str | None]:
    normalized_provider = provider.strip().lower()
    normalized_account_id = account_id.strip()
    normalized_fallback = fallback_account_id.strip() if fallback_account_id else None

    if normalized_provider not in EXTERNAL_READY_CANDIDATE_CHANNELS:
        raise HTTPException(status_code=400, detail='Unsupported channel provider')
    if not normalized_account_id:
        raise HTTPException(status_code=400, detail='account_id is required')

    duplicate_query = db.query(ChannelAccount).filter(ChannelAccount.account_id == normalized_account_id)
    if current_row is not None:
        duplicate_query = duplicate_query.filter(ChannelAccount.id != current_row.id)
    if duplicate_query.first() is not None:
        raise HTTPException(status_code=400, detail='Channel account already exists')

    market = None
    if market_id is not None:
        market = db.query(Market).filter(Market.id == market_id, Market.is_active.is_(True)).first()
        if market is None:
            raise HTTPException(status_code=400, detail='Market not found or inactive')

    if normalized_fallback:
        if normalized_fallback == normalized_account_id:
            raise HTTPException(status_code=400, detail='Fallback cannot point to itself')
        fallback_row = db.query(ChannelAccount).filter(ChannelAccount.account_id == normalized_fallback).first()
        if fallback_row is None:
            raise HTTPException(status_code=400, detail='Fallback channel account not found')
        if current_row is not None and fallback_row.id == current_row.id:
            raise HTTPException(status_code=400, detail='Fallback cannot point to itself')
        if fallback_row.provider.strip().lower() != normalized_provider:
            raise HTTPException(status_code=400, detail='Fallback provider must match primary provider')
        if market_id is None and fallback_row.market_id is not None:
            raise HTTPException(status_code=400, detail='Global primary account cannot fallback to market-specific account')
        if market_id is not None and fallback_row.market_id not in (None, market_id):
            raise HTTPException(status_code=400, detail='Fallback market must be global or match primary market')

    return normalized_provider, normalized_account_id, normalized_fallback



@router.get('/capabilities/catalog', response_model=list[str])
def list_capability_catalog(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_can_manage_users(current_user, db)
    return sorted(ALL_CAPABILITIES)


SENSITIVE_AUDIT_KEY_FRAGMENTS = ('password', 'secret', 'token', 'credential', 'authorization', 'api_key', 'private_key')


def _audit_value(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return _redact_sensitive_audit_value(json.loads(raw))
    except json.JSONDecodeError:
        return raw


def _redact_sensitive_audit_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in SENSITIVE_AUDIT_KEY_FRAGMENTS):
                redacted[key] = '[redacted]'
            else:
                redacted[key] = _redact_sensitive_audit_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_audit_value(item) for item in value]
    return value


def _is_high_risk_capability(capability: str) -> bool:
    if capability.startswith('tool:'):
        return True
    return capability.endswith((
        '.accept',
        '.assign',
        '.close',
        '.control',
        '.end',
        '.escalate',
        '.manage',
        '.reject',
        '.send',
        '.write',
    ))


def _serialize_security_audit_log(row: AdminAuditLog, actor_by_id: dict[int, User]) -> AdminAuditLogRead:
    actor = actor_by_id.get(row.actor_id) if row.actor_id is not None else None
    return AdminAuditLogRead(
        id=row.id,
        actor_id=row.actor_id,
        actor_username=actor.username if actor else None,
        actor_display_name=actor.display_name if actor else None,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        old_value=_audit_value(row.old_value_json),
        new_value=_audit_value(row.new_value_json),
        created_at=row.created_at,
    )


@router.get('/security-audit', response_model=SecurityAuditRead)
def security_audit(limit: int = 30, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_view_security_audit(current_user, db)
    clamped_limit = min(max(limit, 1), 100)
    current_capabilities = resolve_capabilities(current_user, db)

    users = db.query(User).order_by(User.is_active.desc(), User.role.asc(), User.username.asc()).all()
    override_rows = db.query(UserCapabilityOverride).all()
    override_counts: dict[int, int] = {}
    high_risk_overrides = 0
    for override in override_rows:
        override_counts[override.user_id] = override_counts.get(override.user_id, 0) + 1
        if override.allowed and _is_high_risk_capability(override.capability):
            high_risk_overrides += 1

    user_lens = [
        SecurityCapabilityUserRead(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            effective_capabilities=sorted(resolve_capabilities(user, db)),
            override_count=override_counts.get(user.id, 0),
            high_risk_count=sum(1 for capability in resolve_capabilities(user, db) if _is_high_risk_capability(capability)),
        )
        for user in users
    ]

    audit_rows = db.query(AdminAuditLog).order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc()).limit(clamped_limit).all()
    actor_ids = {row.actor_id for row in audit_rows if row.actor_id is not None}
    actors = {row.id: row for row in db.query(User).filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
    recent_audit = [_serialize_security_audit_log(row, actors) for row in audit_rows]

    recent_cutoff = utc_now() - timedelta(hours=24)
    summary = SecurityAuditSummaryRead(
        total_users=len(users),
        active_users=sum(1 for user in users if user.is_active),
        inactive_users=sum(1 for user in users if not user.is_active),
        admin_users=sum(1 for user in users if user.role == UserRole.admin),
        auditor_users=sum(1 for user in users if user.role == UserRole.auditor),
        high_risk_overrides=high_risk_overrides,
        recent_audit_24h=db.query(func.count(AdminAuditLog.id)).filter(AdminAuditLog.created_at >= recent_cutoff).scalar() or 0,
        catalog_size=len(ALL_CAPABILITIES),
        read_only=CAP_USER_MANAGE not in current_capabilities,
    )
    return SecurityAuditRead(
        capability_catalog=sorted(ALL_CAPABILITIES),
        users=user_lens,
        recent_audit=recent_audit,
        summary=summary,
    )


@router.get('/users/{user_id}/capabilities', response_model=UserCapabilityMatrixRead)
def get_user_capabilities(user_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
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
    ensure_can_manage_users(current_user, db)
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
    ensure_can_manage_users(current_user, db)
    row = db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user_id, UserCapabilityOverride.capability == capability).first()
    if not row:
        raise HTTPException(status_code=404, detail='Capability override not found')
    with managed_session(db):
        db.delete(row)
    return {'ok': True}


@router.get('/integration-clients', response_model=list[IntegrationClientRead])
def list_integration_clients(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    rows = db.query(IntegrationClient).order_by(IntegrationClient.name.asc()).all()
    return [IntegrationClientRead.model_validate(x) for x in rows]


@router.get('/markets', response_model=list[MarketRead])
def list_markets(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_markets(current_user, db)
    rows = db.query(Market).order_by(Market.country_code.asc(), Market.name.asc()).all()
    return [MarketRead.model_validate(x) for x in rows]


@router.post('/markets', response_model=MarketRead)
def create_market(payload: MarketCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_markets(current_user, db)
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
    ensure_can_manage_markets(current_user, db)
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


@router.get('/external_channel/links', response_model=list[ExternalChannelConversationRead])
def list_external_channel_links(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Read-only access to historical ExternalChannel linkage."""

    ensure_can_manage_runtime(current_user, db)
    rows = db.query(ExternalChannelConversationLink).order_by(ExternalChannelConversationLink.updated_at.desc()).all()
    return [ExternalChannelConversationRead.model_validate(row) for row in rows]


def get_queue_summary(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    return QueueSummaryRead(
        pending_outbound=db.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.pending).count(),
        dead_outbound=db.query(TicketOutboundMessage).filter(TicketOutboundMessage.status == MessageStatus.dead).count(),
        pending_jobs=db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.pending).count(),
        dead_jobs=db.query(BackgroundJob).filter(BackgroundJob.status == JobStatus.dead).count(),
        external_channel_links=db.query(ExternalChannelConversationLink).count(),
        external_channel_transcript_messages=db.query(ExternalChannelTranscriptMessage).count(),
        external_channel_unresolved_events=db.query(ExternalChannelUnresolvedEvent).count(),
    )


@router.get('/jobs', response_model=list[BackgroundJobRead])
def list_background_jobs(status: str | None = None, limit: int = 100, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    query = db.query(BackgroundJob).order_by(BackgroundJob.created_at.desc())
    if status:
        query = query.filter(BackgroundJob.status == status)
    rows = query.limit(limit).all()
    return [BackgroundJobRead.model_validate(x) for x in rows]


@router.get('/production-readiness', response_model=ProductionReadinessRead)
def production_readiness(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    warnings: list[str] = []
    if not settings.is_postgres:
        warnings.append('DATABASE_URL is not PostgreSQL; stage/prod cutover is still pending')
    if settings.storage_backend == 'local':
        warnings.append('STORAGE_BACKEND=local; object storage cutover is still pending')
    if settings.external_channel_deployment_mode != 'disabled' or settings.external_channel_transport != 'disabled':
        warnings.append('Legacy ExternalChannel runtime env vars are still enabled')
    if not settings.metrics_enabled:
        warnings.append('Metrics are disabled')
    if db.bind and not db.bind.dialect.name.startswith('postgresql'):
        warnings.append('Current runtime DB dialect is not PostgreSQL')
    if settings.external_channel_sync_enabled and not settings.external_channel_inbound_auto_sync_enabled and not settings.external_channel_event_driver_enabled:
        warnings.append('ExternalChannel sync is enabled but no inbound auto-sync/event driver/manual job producer is active')
    outbound_email_active_accounts = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.is_active.is_(True)).count()
    outbound_email_successful_test_send_accounts = count_active_successful_tested_accounts(
        db,
        max_age_hours=settings.outbound_email_test_send_max_age_hours,
    )
    if settings.outbound_email_production_pilot_enabled and outbound_email_successful_test_send_accounts < 1:
        warnings.append(
            f'OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true requires at least one active SMTP account with a successful test-send in the last {settings.outbound_email_test_send_max_age_hours} hours'
        )
    return ProductionReadinessRead(
        app_env=settings.app_env,
        database_url_scheme=settings.database_url.split(':', 1)[0],
        is_postgres=settings.is_postgres,
        storage_backend=settings.storage_backend,
        external_channel_transport=settings.external_channel_transport,
        metrics_enabled=settings.metrics_enabled,
        external_channel_sync_enabled=settings.external_channel_sync_enabled,
        external_channel_inbound_auto_sync_enabled=settings.external_channel_inbound_auto_sync_enabled,
        external_channel_links_count=db.query(ExternalChannelConversationLink).count(),
        external_channel_transcript_messages_count=db.query(ExternalChannelTranscriptMessage).count(),
        external_channel_unresolved_events_count=db.query(ExternalChannelUnresolvedEvent).count(),
        outbound_email_production_pilot_enabled=settings.outbound_email_production_pilot_enabled,
        outbound_email_active_accounts=outbound_email_active_accounts,
        outbound_email_successful_test_send_accounts=outbound_email_successful_test_send_accounts,
        outbound_email_test_send_max_age_hours=settings.outbound_email_test_send_max_age_hours,
        warnings=warnings,
    )


@router.get('/signoff-checklist')
def signoff_checklist(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    checks['postgres_configured'] = settings.is_postgres
    if not checks['postgres_configured']:
        warnings.append('DATABASE_URL is not PostgreSQL')

    checks['storage_not_local'] = settings.storage_backend != 'local'
    if not checks['storage_not_local']:
        warnings.append('STORAGE_BACKEND is local')

    checks['legacy_external_channel_disabled'] = settings.external_channel_deployment_mode == 'disabled' and settings.external_channel_transport == 'disabled'
    if not checks['legacy_external_channel_disabled']:
        warnings.append('Legacy ExternalChannel runtime is still enabled')

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

    if settings.external_channel_event_driver_enabled:
        warnings.append('Legacy ExternalChannel event driver is still enabled')

    outbound_email_successful_test_send_accounts = count_active_successful_tested_accounts(
        db,
        max_age_hours=settings.outbound_email_test_send_max_age_hours,
    )
    checks['outbound_email_pilot_test_send_success'] = (
        not settings.outbound_email_production_pilot_enabled
        or outbound_email_successful_test_send_accounts > 0
    )
    if not checks['outbound_email_pilot_test_send_success']:
        warnings.append(
            f'OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true but no active SMTP account has a successful test-send in the last {settings.outbound_email_test_send_max_age_hours} hours'
        )
    return {
        'status': 'ready' if not warnings else 'not_ready',
        'checks': checks,
        'warnings': warnings,
    }


@router.get('/channel-accounts', response_model=list[ChannelAccountRead])
def list_channel_accounts(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    rows = db.query(ChannelAccount).order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc()).all()
    return [ChannelAccountRead.model_validate(x) for x in rows]


@router.post('/channel-accounts', response_model=ChannelAccountRead)
def create_channel_account(payload: ChannelAccountCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    normalized_provider, normalized_account_id, normalized_fallback = _validate_channel_account_payload(
        db,
        provider=payload.provider,
        account_id=payload.account_id,
        market_id=payload.market_id,
        fallback_account_id=payload.fallback_account_id,
    )
    with managed_session(db):
        row = ChannelAccount(
            provider=normalized_provider,
            account_id=normalized_account_id,
            display_name=payload.display_name.strip() if payload.display_name else None,
            market_id=payload.market_id,
            priority=payload.priority,
            fallback_account_id=normalized_fallback,
            health_status='unknown',
        )
        db.add(row)
        db.flush()
    db.refresh(row)
    return ChannelAccountRead.model_validate(row)


@router.patch('/channel-accounts/{account_id}', response_model=ChannelAccountRead)
def update_channel_account(account_id: int, payload: ChannelAccountUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(ChannelAccount).filter(ChannelAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='Channel account not found')
    target_market_id = payload.market_id if payload.market_id is not None else row.market_id
    target_fallback_account_id = payload.fallback_account_id if payload.fallback_account_id is not None else row.fallback_account_id
    _, _, normalized_fallback = _validate_channel_account_payload(
        db,
        provider=row.provider,
        account_id=row.account_id,
        market_id=target_market_id,
        fallback_account_id=target_fallback_account_id,
        current_row=row,
    )
    with managed_session(db):
        data = payload.model_dump(exclude_unset=True)
        if 'display_name' in data and data['display_name'] is not None:
            data['display_name'] = data['display_name'].strip()
        if 'fallback_account_id' in data:
            data['fallback_account_id'] = normalized_fallback
        for key, value in data.items():
            setattr(row, key, value)
        db.flush()
    db.refresh(row)
    return ChannelAccountRead.model_validate(row)




@router.get('/ai-configs', response_model=list[AIConfigResourceRead])
def list_ai_configs(config_type: str | None = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    rows = list_admin_resources(db, config_type=config_type)
    return [AIConfigResourceRead.model_validate(row) for row in rows]


@router.post('/ai-configs', response_model=AIConfigResourceRead)
def create_ai_config(payload: AIConfigResourceCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = create_ai_config_resource(db, payload, current_user)
    db.refresh(row)
    return AIConfigResourceRead.model_validate(row)


@router.patch('/ai-configs/{resource_id}', response_model=AIConfigResourceRead)
def update_ai_config(resource_id: int, payload: AIConfigResourceUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    with managed_session(db):
        row = update_ai_config_resource(db, row, payload, current_user)
    db.refresh(row)
    return AIConfigResourceRead.model_validate(row)


@router.post('/ai-configs/{resource_id}/publish', response_model=AIConfigVersionRead)
def publish_ai_config(resource_id: int, payload: AIConfigPublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    with managed_session(db):
        version_row = publish_resource(db, row, current_user, notes=payload.notes)
    db.refresh(version_row)
    return AIConfigVersionRead.model_validate(version_row)


@router.get('/ai-configs/{resource_id}/versions', response_model=list[AIConfigVersionRead])
def get_ai_config_versions(resource_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    return [AIConfigVersionRead.model_validate(item) for item in list_ai_config_versions(db, resource_id)]


@router.post('/ai-configs/{resource_id}/rollback/{version}', response_model=AIConfigVersionRead)
def rollback_ai_config(resource_id: int, version: int, payload: AIConfigPublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(AIConfigResource).filter(AIConfigResource.id == resource_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='AI config not found')
    with managed_session(db):
        version_row = rollback_resource(db, row, version, current_user, notes=payload.notes)
    db.refresh(version_row)
    return AIConfigVersionRead.model_validate(version_row)

@router.get('/bulletins', response_model=list[MarketBulletinRead])
def list_bulletins(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_bulletins(current_user, db)
    rows = db.query(MarketBulletin).order_by(MarketBulletin.updated_at.desc()).all()
    return [MarketBulletinRead.model_validate(x) for x in rows]


@router.post('/bulletins', response_model=MarketBulletinRead)
def create_bulletin(payload: MarketBulletinCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_bulletins(current_user, db)
    with managed_session(db):
        row = MarketBulletin(
            market_id=payload.market_id,
            country_code=normalize_bulletin_country_code(payload.country_code),
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
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='bulletin.create',
            target_type='market_bulletin',
            target_id=row.id,
            old_value=None,
            new_value=bulletin_audit_snapshot(row),
        )
    db.refresh(row)
    return MarketBulletinRead.model_validate(row)


@router.post('/bulletins/impact-preview', response_model=MarketBulletinImpactPreviewRead)
def preview_bulletin_impact(payload: MarketBulletinImpactPreviewRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_bulletins(current_user, db)
    return MarketBulletinImpactPreviewRead.model_validate(
        build_bulletin_impact_preview(
            db,
            market_id=payload.market_id,
            country_code=payload.country_code,
            channels_csv=payload.channels_csv,
            audience=payload.audience,
            auto_inject_to_ai=payload.auto_inject_to_ai,
            is_active=payload.is_active,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
    )


@router.patch('/bulletins/{bulletin_id}', response_model=MarketBulletinRead)
def update_bulletin(bulletin_id: int, payload: MarketBulletinUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_bulletins(current_user, db)
    row = db.query(MarketBulletin).filter(MarketBulletin.id == bulletin_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='Bulletin not found')
    before = bulletin_audit_snapshot(row)
    with managed_session(db):
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key == 'country_code':
                value = normalize_bulletin_country_code(value)
            setattr(row, key, value)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='bulletin.update',
            target_type='market_bulletin',
            target_id=row.id,
            old_value=before,
            new_value=bulletin_audit_snapshot(row),
        )
    db.refresh(row)
    return MarketBulletinRead.model_validate(row)


@router.post('/users', response_model=UserRead)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    normalized_username = _normalize_username(payload.username)
    normalized_display_name = _normalize_display_name(payload.display_name)
    normalized_email = _normalize_email(payload.email)
    _validate_password_length(payload.password)
    _ensure_user_uniqueness(db, username=normalized_username, email=normalized_email)

    with managed_session(db):
        new_user = User(
            username=normalized_username,
            display_name=normalized_display_name,
            email=normalized_email,
            password_hash=hash_password(payload.password),
            role=payload.role,
            team_id=payload.team_id,
            is_active=True,
        )
        db.add(new_user)
        db.flush()
        _apply_user_capability_overrides(db, user_id=new_user.id, role=new_user.role, requested_capabilities=payload.capabilities or [])
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action='user.create',
            target_type='user',
            target_id=new_user.id,
            old_value=None,
            new_value={'username': new_user.username, 'email': new_user.email, 'role': new_user.role.value, 'team_id': new_user.team_id},
        )
    db.refresh(new_user)
    return _serialize_user(new_user, db)


def list_admin_users(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    rows = db.query(User).order_by(User.is_active.desc(), User.role.asc(), User.username.asc()).all()
    return [_serialize_user(row, db) for row in rows]

@router.patch('/users/{user_id}', response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    row = db.query(User).filter(User.id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail='User not found')
    if row.id == current_user.id and payload.role is not None and payload.role not in {UserRole.admin, UserRole.manager}:
        raise HTTPException(status_code=400, detail='当前管理员不能把自己降权到无治理能力')
    if row.role == UserRole.admin and payload.role is not None and payload.role != UserRole.admin and _is_last_active_admin(db, row.id):
        raise HTTPException(status_code=400, detail='不能降权最后一个 active admin')
    next_username = row.username
    next_email = _normalize_email(payload.email) if payload.email is not None else row.email
    _ensure_user_uniqueness(db, username=next_username, email=next_email, exclude_user_id=row.id)
    old_value = {'username': row.username, 'display_name': row.display_name, 'email': row.email, 'role': row.role.value, 'team_id': row.team_id, 'is_active': row.is_active}
    with managed_session(db):
        if payload.display_name is not None:
            row.display_name = _normalize_display_name(payload.display_name)
        if payload.email is not None:
            row.email = next_email
        if payload.role is not None:
            row.role = payload.role
        if payload.team_id is not None:
            row.team_id = payload.team_id
        if payload.capabilities is not None:
            _apply_user_capability_overrides(db, user_id=row.id, role=row.role, requested_capabilities=payload.capabilities)
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.update', target_type='user', target_id=row.id, old_value=old_value, new_value={'username': row.username, 'display_name': row.display_name, 'email': row.email, 'role': row.role.value, 'team_id': row.team_id, 'is_active': row.is_active})
    db.refresh(row)
    return _serialize_user(row, db)

@router.post('/users/{user_id}/activate', response_model=UserRead)
def activate_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    row = db.query(User).filter(User.id == user_id).first()
    if not row: raise HTTPException(404, "User not found")
    with managed_session(db):
        row.is_active = True
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.activate', target_type='user', target_id=row.id, old_value={'is_active': False}, new_value={'is_active': True})
    db.refresh(row)
    return _serialize_user(row, db)

@router.post('/users/{user_id}/deactivate', response_model=UserRead)
def deactivate_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    row = db.query(User).filter(User.id == user_id).first()
    if not row: raise HTTPException(404, "User not found")
    if row.id == current_user.id: raise HTTPException(400, "Cannot deactivate self")
    if row.role == UserRole.admin and _is_last_active_admin(db, row.id): raise HTTPException(400, "Cannot deactivate last admin")
    with managed_session(db):
        row.is_active = False
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.deactivate', target_type='user', target_id=row.id, old_value={'is_active': True}, new_value={'is_active': False})
    db.refresh(row)
    return _serialize_user(row, db)

@router.post('/users/{user_id}/reset-password')
def reset_user_password(user_id: int, payload: PasswordResetRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_users(current_user, db)
    row = db.query(User).filter(User.id == user_id).first()
    if not row: raise HTTPException(404, "User not found")
    _validate_password_length(payload.password)
    with managed_session(db):
        row.password_hash = hash_password(payload.password)
        db.flush()
        log_admin_audit(db, actor_id=current_user.id, action='user.reset_password', target_type='user', target_id=row.id, old_value={}, new_value={})
    return {"ok": True}

@router.get('/external_channel/unresolved-events', response_model=list[ExternalChannelUnresolvedEventRead])
def list_unresolved_events(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    rows = db.query(ExternalChannelUnresolvedEvent).order_by(ExternalChannelUnresolvedEvent.created_at.desc()).all()
    return [ExternalChannelUnresolvedEventRead.model_validate(x) for x in rows]
