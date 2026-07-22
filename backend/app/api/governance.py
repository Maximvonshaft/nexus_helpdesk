from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..api.admin import (
    _apply_user_capability_overrides,
    create_market as create_canonical_market,
)
from ..api.deps import get_current_user
from ..db import get_db
from ..models import Market, Team, Tenant, User
from ..models_agent_control import AgentDeployment, AgentRelease, AgentRun
from ..models_control_plane import KnowledgeItem
from ..schemas import MarketCreate
from ..models_governance import (
    AgentDeploymentRevision,
    CountryCatalog,
    KnowledgeImportBatch,
    KnowledgeImportDocument,
    RoleTemplate,
    RoleTemplateAssignment,
)
from ..services import governance_service, knowledge_service
from ..services.agent_release_service import activate_deployment, authoritative_tenant_key
from ..services.audit_service import log_admin_audit
from ..services.credential_policy_service import advance_user_identity_version
from ..services.tenant_authority import stamp_runtime_tenant
from ..services.identity_tenant_scope import actor_tenant_id, apply_tenant_scope
from ..services.knowledge_document_service import read_upload_bytes
from ..services.permissions import (
    ensure_can_manage_ai_configs,
    ensure_can_manage_markets,
    ensure_can_read_ai_configs,
    ensure_can_manage_runtime,
    CAP_USER_MANAGE,
    ensure_can_manage_users,
    resolve_capabilities,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now


router = APIRouter(prefix="/api/governance", tags=["governance"])
_KEY_SAFE_RE = re.compile(r"[^a-z0-9_.-]+")


class RoleTemplateCreateRequest(BaseModel):
    role_key: str = Field(min_length=2, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    base_role: str = Field(default="agent", max_length=32)
    risk_level: str = Field(default="standard", max_length=24)
    capabilities: list[str] = Field(default_factory=list)


class RoleTemplateUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    base_role: str | None = Field(default=None, max_length=32)
    risk_level: str | None = Field(default=None, max_length=24)
    capabilities: list[str] | None = None
    is_active: bool | None = None


class PublishRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class MarketGovernanceCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    status: str = Field(default="active", max_length=24)
    default_currency: str | None = Field(default=None, max_length=3)
    owner_team_id: int | None = None
    data_region: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)
    country_codes: list[str] = Field(min_length=1)
    language_codes: list[str] = Field(min_length=1)


class MarketGovernanceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=24)
    default_currency: str | None = Field(default=None, max_length=3)
    owner_team_id: int | None = None
    data_region: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)
    country_codes: list[str] | None = None
    language_codes: list[str] | None = None
    expected_version: int | None = Field(default=None, ge=0)


class CanaryStartRequest(BaseModel):
    release_id: int = Field(gt=0)
    percent: int = Field(default=5, ge=1, le=99)
    reason: str = Field(min_length=2, max_length=2000)


class CanaryAdjustRequest(BaseModel):
    percent: int = Field(ge=1, le=99)
    reason: str = Field(min_length=2, max_length=2000)


class CanaryActionRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=2000)


def _tenant_id_string(db: Session, current_user: User) -> str:
    if current_user.tenant_id is None:
        return "default"
    tenant = db.get(Tenant, current_user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")
    return tenant.tenant_key




def _find_visible_knowledge_import_duplicate(
    db: Session,
    *,
    tenant_id: str,
    sha256: str,
    market_id: int | None,
    channel: str,
    audience_scope: str,
    language: str | None,
) -> KnowledgeImportDocument | None:
    return (
        db.query(KnowledgeImportDocument)
        .join(
            KnowledgeItem,
            KnowledgeImportDocument.knowledge_item_id == KnowledgeItem.id,
        )
        .filter(
            KnowledgeImportDocument.tenant_id == tenant_id,
            KnowledgeImportDocument.sha256 == sha256,
            KnowledgeImportDocument.status == "draft_created",
            KnowledgeItem.tenant_id == tenant_id,
            KnowledgeItem.market_id == market_id,
            KnowledgeItem.channel == channel,
            KnowledgeItem.audience_scope == audience_scope,
            KnowledgeItem.language == language,
        )
        .order_by(KnowledgeImportDocument.id.asc())
        .first()
    )


def _active_governor_ids(db: Session, tenant_id: int | None) -> set[int]:
    users = (
        apply_tenant_scope(db.query(User), User, tenant_id)
        .filter(User.is_active.is_(True))
        .order_by(User.id.asc())
        .all()
    )
    return {
        user.id
        for user in users
        if CAP_USER_MANAGE in resolve_capabilities(user, db)
    }


def _ensure_governance_access_survives(
    db: Session,
    *,
    tenant_id: int | None,
    losing_user_ids: set[int],
) -> None:
    if not losing_user_ids:
        return
    if _active_governor_ids(db, tenant_id) - losing_user_ids:
        return
    raise HTTPException(status_code=409, detail="cannot_remove_last_governance_access")


def _role_template_assigned_users(
    db: Session, *, tenant_id: int | None, template_id: int
) -> list[User]:
    return (
        apply_tenant_scope(db.query(User), User, tenant_id)
        .join(RoleTemplateAssignment, RoleTemplateAssignment.user_id == User.id)
        .filter(RoleTemplateAssignment.template_id == template_id)
        .order_by(User.id.asc())
        .all()
    )


def _template_for_actor(
    db: Session, current_user: User, template_id: int, *, require_manageable: bool = False
) -> RoleTemplate:
    tenant_id = actor_tenant_id(db, current_user)
    row = db.get(RoleTemplate, template_id)
    if row is None or row.tenant_id not in {tenant_id, None}:
        raise HTTPException(status_code=404, detail="role_template_not_found")
    if require_manageable and (row.tenant_id != tenant_id or row.is_system_protected):
        raise HTTPException(status_code=403, detail="role_template_read_only")
    return row


@router.get("/capabilities")
def capability_catalog(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    from ..services.permissions import ALL_CAPABILITIES

    return sorted(ALL_CAPABILITIES)


@router.get("/role-templates")
def role_templates(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    return governance_service.list_role_templates(db, current_user)


@router.post("/role-templates")
def create_role_template(
    payload: RoleTemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    with managed_session(db):
        row = governance_service.create_role_template(
            db,
            actor=current_user,
            role_key=payload.role_key,
            display_name=payload.display_name,
            description=payload.description,
            base_role=payload.base_role,
            risk_level=payload.risk_level,
            capabilities=payload.capabilities,
        )
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="role_template.create",
            target_type="role_template",
            target_id=row.id,
            old_value=None,
            new_value=governance_service.role_template_payload(row),
        )
    db.refresh(row)
    return governance_service.role_template_payload(row)


@router.patch("/role-templates/{template_id}")
def update_role_template(
    template_id: int,
    payload: RoleTemplateUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    row = _template_for_actor(db, current_user, template_id, require_manageable=True)
    before = governance_service.role_template_payload(row)
    with managed_session(db):
        row = governance_service.update_role_template(
            db,
            row=row,
            actor=current_user,
            **payload.model_dump(exclude_unset=True),
        )
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="role_template.update",
            target_type="role_template",
            target_id=row.id,
            old_value=before,
            new_value=governance_service.role_template_payload(row),
        )
    db.refresh(row)
    return governance_service.role_template_payload(row)


@router.post("/role-templates/{template_id}/publish")
def publish_role_template(
    template_id: int,
    payload: PublishRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    row = _template_for_actor(db, current_user, template_id, require_manageable=True)
    tenant_id = actor_tenant_id(db, current_user)
    capabilities = governance_service.clean_capabilities(
        list(row.draft_capabilities_json or [])
    )
    base_role = governance_service.validate_base_role(row.base_role)
    assigned_users = _role_template_assigned_users(
        db, tenant_id=tenant_id, template_id=row.id
    )
    losing_governors = {
        user.id
        for user in assigned_users
        if user.is_active
        and CAP_USER_MANAGE in resolve_capabilities(user, db)
        and CAP_USER_MANAGE not in capabilities
    }
    if current_user.id in losing_governors:
        raise HTTPException(
            status_code=409, detail="cannot_remove_own_governance_access"
        )
    _ensure_governance_access_survives(
        db, tenant_id=tenant_id, losing_user_ids=losing_governors
    )

    with managed_session(db):
        version = governance_service.publish_role_template(
            db, row=row, actor=current_user, notes=payload.notes
        )
        now = utc_now()
        for user in assigned_users:
            user.role = base_role
            _apply_user_capability_overrides(
                db,
                user_id=user.id,
                role=user.role,
                requested_capabilities=capabilities,
            )
            assignment = db.get(RoleTemplateAssignment, user.id)
            if assignment is None:
                raise RuntimeError("role_template_assignment_missing")
            assignment.template_version = version.version
            assignment.assigned_by = current_user.id
            assignment.assigned_at = now
            advance_user_identity_version(user)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="role_template.publish",
            target_type="role_template",
            target_id=row.id,
            old_value={"published_version": version.version - 1},
            new_value={
                "published_version": version.version,
                "affected_users": len(assigned_users),
                "sessions_revoked": bool(assigned_users),
            },
        )
    return {
        "template_id": row.id,
        "version": version.version,
        "published_at": version.published_at,
        "affected_users": len(assigned_users),
    }

@router.post("/role-templates/{template_id}/apply/{user_id}")
def apply_role_template(
    template_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    template = _template_for_actor(db, current_user, template_id)
    if not template.is_active or template.published_version <= 0:
        raise HTTPException(status_code=409, detail="publish_role_template_before_assignment")
    tenant_id = actor_tenant_id(db, current_user)
    user = (
        apply_tenant_scope(db.query(User), User, tenant_id)
        .filter(User.id == user_id, User.is_active.is_(True))
        .one_or_none()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    base_role, capabilities = governance_service.role_template_version_values(
        db, template_id=template.id, version=template.published_version
    )
    currently_governs = CAP_USER_MANAGE in resolve_capabilities(user, db)
    will_govern = CAP_USER_MANAGE in capabilities
    if user.id == current_user.id and not will_govern:
        raise HTTPException(status_code=409, detail="cannot_remove_own_governance_access")
    if currently_governs and not will_govern:
        _ensure_governance_access_survives(
            db, tenant_id=tenant_id, losing_user_ids={user.id}
        )
    before = {
        "role": user.role.value,
        "capabilities": sorted(resolve_capabilities(user, db)),
        "assignment": governance_service.role_assignment_payload(db, user),
    }
    with managed_session(db):
        user.role = base_role
        _apply_user_capability_overrides(
            db,
            user_id=user.id,
            role=user.role,
            requested_capabilities=capabilities,
        )
        assignment = db.get(RoleTemplateAssignment, user.id)
        if assignment is None:
            assignment = RoleTemplateAssignment(user_id=user.id)
            db.add(assignment)
        assignment.template_id = template.id
        assignment.template_version = template.published_version
        assignment.assigned_by = current_user.id
        assignment.assigned_at = utc_now()
        advance_user_identity_version(user)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="role_template.apply",
            target_type="user",
            target_id=user.id,
            old_value=before,
            new_value={
                "role": user.role.value,
                "capabilities": capabilities,
                "template_id": template.id,
                "template_version": template.published_version,
                "sessions_revoked": True,
            },
        )
    db.refresh(user)
    return {
        "user_id": user.id,
        "role": user.role.value,
        "capabilities": sorted(resolve_capabilities(user, db)),
        "assignment": governance_service.role_assignment_payload(db, user),
    }


@router.get("/role-template-assignments")
def role_template_assignments(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    tenant_id = actor_tenant_id(db, current_user)
    users = (
        apply_tenant_scope(db.query(User), User, tenant_id)
        .order_by(User.display_name.asc())
        .all()
    )
    return [
        {
            "user_id": user.id,
            "display_name": user.display_name,
            "username": user.username,
            "assignment": governance_service.role_assignment_payload(db, user),
        }
        for user in users
    ]


@router.get("/countries")
def countries(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_markets(current_user, db)
    rows = (
        db.query(CountryCatalog)
        .filter(CountryCatalog.is_available.is_(True))
        .order_by(CountryCatalog.canonical_name.asc())
        .all()
    )
    return [governance_service.country_payload(row) for row in rows]


@router.get("/market-teams")
def market_teams(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_markets(current_user, db)
    tenant_id = actor_tenant_id(db, current_user)
    rows = (
        apply_tenant_scope(db.query(Team), Team, tenant_id)
        .filter(Team.is_active.is_(True))
        .order_by(Team.name.asc())
        .all()
    )
    return [{"id": row.id, "name": row.name} for row in rows]


@router.post("/markets")
def create_market(
    payload: MarketGovernanceCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_markets(current_user, db)
    country_codes = governance_service.validate_country_codes(db, payload.country_codes)
    language_codes = governance_service.validate_languages(payload.language_codes)
    timezone = governance_service.validate_timezone(payload.timezone)
    currency = governance_service.validate_currency(payload.default_currency)
    # Validate the optional owner before the canonical Market row is committed.
    if payload.owner_team_id is not None:
        tenant_id = actor_tenant_id(db, current_user)
        owner = (
            apply_tenant_scope(db.query(Team), Team, tenant_id)
            .filter(Team.id == payload.owner_team_id, Team.is_active.is_(True))
            .one_or_none()
        )
        if owner is None:
            raise HTTPException(status_code=400, detail="owner_team_not_available")

    created = create_canonical_market(
        MarketCreate(
            code=payload.code,
            name=payload.name,
            country_code=country_codes[0],
            language_code=language_codes[0],
            timezone=timezone,
        ),
        db=db,
        current_user=current_user,
    )
    market = db.get(Market, created.id)
    if market is None:
        raise RuntimeError("canonical_market_creation_missing")
    try:
        with managed_session(db):
            stamp_runtime_tenant(market, actor_tenant_id(db, current_user))
            governance_service.update_market_governance(
                db,
                market=market,
                actor=current_user,
                status=payload.status,
                default_currency=currency,
                owner_team_id=payload.owner_team_id,
                data_region=payload.data_region,
                notes=payload.notes,
                country_codes=country_codes,
                language_codes=language_codes,
            )
            result = governance_service.market_payload(db, market)
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action="market.governance.create",
                target_type="market",
                target_id=market.id,
                old_value=None,
                new_value=result,
            )
        db.refresh(market)
        return governance_service.market_payload(db, market)
    except Exception:
        db.rollback()
        created_market = db.get(Market, created.id)
        if created_market is not None:
            with managed_session(db):
                db.delete(created_market)
        raise


@router.get("/markets")
def markets(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_markets(current_user, db)
    return governance_service.list_markets_for_governance(db, current_user)


@router.get("/markets/{market_id}/impact")
def market_impact(
    market_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_markets(current_user, db)
    governance_service.market_for_actor(db, current_user, market_id)
    return governance_service.market_impact(db, market_id)


@router.patch("/markets/{market_id}")
def update_market(
    market_id: int,
    payload: MarketGovernanceUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_markets(current_user, db)
    market = governance_service.market_for_actor(db, current_user, market_id)
    values = payload.model_dump(exclude_unset=True)
    expected_version = values.pop("expected_version", None)
    with managed_session(db):
        before = governance_service.market_payload(db, market)
        governance_service.update_market_governance(
            db,
            market=market,
            actor=current_user,
            expected_version=expected_version,
            **values,
        )
        after = governance_service.market_payload(db, market)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="market.governance.update",
            target_type="market",
            target_id=market.id,
            old_value=before,
            new_value=after,
        )
    db.refresh(market)
    return governance_service.market_payload(db, market)


def _safe_knowledge_key(batch_id: int, position: int, filename: str) -> str:
    stem = Path(filename or "document").stem.lower()
    stem = _KEY_SAFE_RE.sub("-", stem).strip("-.")[:60] or "document"
    return f"import.{batch_id}.{position}.{stem}"[:120]


def _import_document_payload(row: KnowledgeImportDocument) -> dict:
    return {
        "id": row.id,
        "position": row.position,
        "file_name": row.original_file_name,
        "sha256": row.sha256,
        "status": row.status,
        "knowledge_item_id": row.knowledge_item_id,
        "duplicate_of_document_id": row.duplicate_of_document_id,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at,
    }


def _import_batch_payload(db: Session, row: KnowledgeImportBatch) -> dict:
    documents = (
        db.query(KnowledgeImportDocument)
        .filter(KnowledgeImportDocument.batch_id == row.id)
        .order_by(KnowledgeImportDocument.position.asc())
        .all()
    )
    return {
        "id": row.id,
        "status": row.status,
        "total_files": row.total_files,
        "succeeded_files": row.succeeded_files,
        "failed_files": row.failed_files,
        "duplicate_files": row.duplicate_files,
        "market_id": row.market_id,
        "channel": row.channel,
        "audience_scope": row.audience_scope,
        "language": row.language,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
        "documents": [_import_document_payload(item) for item in documents],
    }


@router.get("/knowledge-imports")
def list_knowledge_imports(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    tenant_key = _tenant_id_string(db, current_user)
    rows = (
        db.query(KnowledgeImportBatch)
        .filter(KnowledgeImportBatch.tenant_id == tenant_key)
        .order_by(KnowledgeImportBatch.created_at.desc(), KnowledgeImportBatch.id.desc())
        .limit(min(max(limit, 1), 100))
        .all()
    )
    return [_import_batch_payload(db, row) for row in rows]


@router.post("/knowledge-imports")
def create_knowledge_import(
    files: Annotated[list[UploadFile], File(description="最多 20 个知识文件")],
    market_id: Annotated[int | None, Form()] = None,
    channel: Annotated[str | None, Form()] = None,
    audience_scope: Annotated[str, Form()] = "customer",
    language: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    if not files or len(files) > 20:
        raise HTTPException(status_code=400, detail="knowledge_import_requires_1_to_20_files")
    tenant_key = _tenant_id_string(db, current_user)
    normalized_channel = str(channel or "all").strip().lower() or "all"
    if normalized_channel not in {"all", "webchat", "whatsapp", "email", "voice", "website"}:
        raise HTTPException(status_code=400, detail="knowledge_import_channel_invalid")
    normalized_audience = str(audience_scope or "customer").strip().lower() or "customer"
    if normalized_audience not in {"customer", "internal"}:
        raise HTTPException(status_code=400, detail="knowledge_import_audience_invalid")
    normalized_language = str(language).strip().lower() if language else None
    if normalized_language:
        normalized_language = governance_service.validate_languages([normalized_language])[0]
    if market_id is not None:
        market = governance_service.market_for_actor(db, current_user, market_id)
        if not market.is_active:
            raise HTTPException(status_code=409, detail="knowledge_import_market_inactive")
    with managed_session(db):
        batch = KnowledgeImportBatch(
            tenant_id=tenant_key,
            status="processing",
            total_files=len(files),
            market_id=market_id,
            channel=normalized_channel,
            audience_scope=normalized_audience,
            language=normalized_language,
            created_by=current_user.id,
        )
        db.add(batch)
        db.flush()

        for position, upload in enumerate(files, start=1):
            filename = Path(upload.filename or f"document-{position}").name
            digest = hashlib.sha256(
                f"{batch.id}:{position}:{filename}".encode("utf-8")
            ).hexdigest()
            try:
                content = read_upload_bytes(upload)
                digest = hashlib.sha256(content).hexdigest()
                upload.file.seek(0)
                duplicate = _find_visible_knowledge_import_duplicate(
                    db,
                    tenant_id=tenant_key,
                    sha256=digest,
                    market_id=market_id,
                    channel=normalized_channel,
                    audience_scope=normalized_audience,
                    language=normalized_language,
                )
                if duplicate is not None:
                    db.add(
                        KnowledgeImportDocument(
                            batch_id=batch.id,
                            tenant_id=tenant_key,
                            position=position,
                            original_file_name=filename,
                            sha256=digest,
                            status="duplicate",
                            duplicate_of_document_id=duplicate.id,
                        )
                    )
                    batch.duplicate_files += 1
                    db.flush()
                    continue

                with db.begin_nested():
                    item = knowledge_service.create_file_item_from_upload(
                        db,
                        file=upload,
                        actor=current_user,
                        item_key=_safe_knowledge_key(batch.id, position, filename),
                        title=filename,
                        market_id=market_id,
                        channel=normalized_channel,
                        audience_scope=batch.audience_scope,
                        language=batch.language,
                    )
                    item.tenant_id = tenant_key
                    item.status = "draft"
                    item.fact_status = "draft"
                    db.flush()
                db.add(
                    KnowledgeImportDocument(
                        batch_id=batch.id,
                        tenant_id=tenant_key,
                        position=position,
                        original_file_name=filename,
                        sha256=digest,
                        status="draft_created",
                        knowledge_item_id=item.id,
                    )
                )
                batch.succeeded_files += 1
                db.flush()
            except HTTPException as exc:
                db.add(
                    KnowledgeImportDocument(
                        batch_id=batch.id,
                        tenant_id=tenant_key,
                        position=position,
                        original_file_name=filename,
                        sha256=digest,
                        status="failed",
                        error_code=f"http_{exc.status_code}",
                        error_message=str(exc.detail)[:2000],
                    )
                )
                batch.failed_files += 1
                db.flush()
            except Exception as exc:
                db.add(
                    KnowledgeImportDocument(
                        batch_id=batch.id,
                        tenant_id=tenant_key,
                        position=position,
                        original_file_name=filename,
                        sha256=digest,
                        status="failed",
                        error_code="document_processing_failed",
                        error_message=type(exc).__name__,
                    )
                )
                batch.failed_files += 1
                db.flush()

        completed_files = batch.succeeded_files + batch.duplicate_files
        if completed_files and not batch.failed_files:
            batch.status = "ready"
        elif completed_files:
            batch.status = "partial"
        else:
            batch.status = "failed"
        batch.completed_at = utc_now()
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="knowledge_import.create",
            target_type="knowledge_import_batch",
            target_id=batch.id,
            old_value=None,
            new_value={
                "total_files": batch.total_files,
                "succeeded_files": batch.succeeded_files,
                "failed_files": batch.failed_files,
                "duplicate_files": batch.duplicate_files,
                "status": batch.status,
            },
        )
    db.refresh(batch)
    return _import_batch_payload(db, batch)


def _deployment_for_actor(
    db: Session, current_user: User, deployment_id: int
) -> tuple[str, AgentDeployment]:
    tenant_key = authoritative_tenant_key(
        db, current_user, requested=None, allow_platform_default=True
    )
    row = (
        db.query(AgentDeployment)
        .filter(
            AgentDeployment.id == deployment_id,
            AgentDeployment.tenant_key == tenant_key,
        )
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent_deployment_not_found")
    return tenant_key, row


def _deployment_snapshot(row: AgentDeployment) -> dict:
    return {
        "active_release_id": row.active_release_id,
        "canary_release_id": row.canary_release_id,
        "canary_percent": row.canary_percent,
        "is_active": row.is_active,
        "environment": row.environment,
        "scope_key": row.scope_key,
    }


def _record_deployment_revision(
    db: Session,
    *,
    deployment: AgentDeployment,
    action: str,
    before: dict,
    reason: str,
    actor_id: int,
) -> AgentDeploymentRevision:
    current = (
        db.query(func.max(AgentDeploymentRevision.revision))
        .filter(AgentDeploymentRevision.deployment_id == deployment.id)
        .scalar()
        or 0
    )
    row = AgentDeploymentRevision(
        deployment_id=deployment.id,
        revision=int(current) + 1,
        action=action,
        before_json=before,
        after_json=_deployment_snapshot(deployment),
        reason=reason,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def _release_or_404(db: Session, release_id: int) -> AgentRelease:
    row = db.get(AgentRelease, release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent_release_not_found")
    return row


def _assert_same_definition(active: AgentRelease, candidate: AgentRelease) -> None:
    if active.definition_id != candidate.definition_id:
        raise HTTPException(status_code=409, detail="canary_release_definition_mismatch")


def _apply_canary_state(
    db: Session,
    *,
    current_user: User,
    deployment: AgentDeployment,
    active: AgentRelease,
    canary: AgentRelease | None,
    percent: int,
    action: str,
    reason: str,
) -> AgentDeploymentRevision:
    before = _deployment_snapshot(deployment)
    activate_deployment(
        db,
        tenant_key=deployment.tenant_key,
        environment=deployment.environment,
        release=active,
        canary_release=canary,
        canary_percent=percent,
        actor_id=current_user.id,
        market_id=deployment.market_id,
        channel=deployment.channel,
        language=deployment.language,
        case_type=deployment.case_type,
    )
    db.flush()
    db.refresh(deployment)
    revision = _record_deployment_revision(
        db,
        deployment=deployment,
        action=action,
        before=before,
        reason=reason,
        actor_id=current_user.id,
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action=f"agent_deployment.{action}",
        target_type="agent_deployment",
        target_id=deployment.id,
        old_value=before,
        new_value={**_deployment_snapshot(deployment), "reason": reason},
    )
    return revision


@router.get("/deployments/{deployment_id}/delivery")
def deployment_delivery(
    deployment_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    tenant_key = authoritative_tenant_key(
        db, current_user, requested=None, allow_platform_default=True
    )
    deployment = (
        db.query(AgentDeployment)
        .filter(
            AgentDeployment.id == deployment_id,
            AgentDeployment.tenant_key == tenant_key,
        )
        .one_or_none()
    )
    if deployment is None:
        raise HTTPException(status_code=404, detail="agent_deployment_not_found")
    cutoff = utc_now() - timedelta(hours=24)
    run_rows = (
        db.query(
            AgentRun.release_id,
            AgentRun.status,
            func.count(AgentRun.id),
            func.avg(AgentRun.elapsed_ms),
        )
        .filter(
            AgentRun.deployment_id == deployment.id,
            AgentRun.started_at >= cutoff,
            AgentRun.release_id.is_not(None),
            AgentRun.fork_kind.is_(None),
        )
        .group_by(AgentRun.release_id, AgentRun.status)
        .all()
    )

    def summarize_release(release_id: int | None) -> dict:
        if release_id is None:
            return {"total": 0, "succeeded": 0, "fallback": 0, "failed": 0, "average_ms": 0}
        selected = [row for row in run_rows if int(row[0]) == int(release_id)]
        counts = {str(status): int(count or 0) for _, status, count, _ in selected}
        total = sum(counts.values())
        weighted_ms = sum(float(average or 0) * int(count or 0) for _, _, count, average in selected)
        return {
            "total": total,
            "succeeded": counts.get("succeeded", 0),
            "fallback": counts.get("fallback", 0),
            "failed": counts.get("failed", 0),
            "average_ms": round(weighted_ms / total) if total else 0,
        }

    stable_health = summarize_release(deployment.active_release_id)
    trial_health = summarize_release(deployment.canary_release_id)
    revisions = (
        db.query(AgentDeploymentRevision)
        .filter(AgentDeploymentRevision.deployment_id == deployment.id)
        .order_by(AgentDeploymentRevision.revision.desc())
        .limit(30)
        .all()
    )
    return {
        "deployment": _deployment_snapshot(deployment),
        "traffic_24h": {
            "stable": stable_health["total"],
            "trial": trial_health["total"],
            "total": stable_health["total"] + trial_health["total"],
        },
        "health_24h": {
            "stable": stable_health,
            "trial": trial_health,
        },
        "revisions": [
            {
                "id": row.id,
                "revision": row.revision,
                "action": row.action,
                "before": row.before_json,
                "after": row.after_json,
                "reason": row.reason,
                "created_by": row.created_by,
                "created_at": row.created_at,
            }
            for row in revisions
        ],
    }


@router.post("/deployments/{deployment_id}/trial/start")
def start_trial(
    deployment_id: int,
    payload: CanaryStartRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        _, deployment = _deployment_for_actor(db, current_user, deployment_id)
        if deployment.canary_release_id:
            raise HTTPException(status_code=409, detail="trial_already_active")
        active = _release_or_404(db, deployment.active_release_id)
        candidate = _release_or_404(db, payload.release_id)
        _assert_same_definition(active, candidate)
        revision = _apply_canary_state(
            db,
            current_user=current_user,
            deployment=deployment,
            active=active,
            canary=candidate,
            percent=payload.percent,
            action="canary_start",
            reason=payload.reason,
        )
    return {"ok": True, "revision": revision.revision, "deployment": _deployment_snapshot(deployment)}


@router.post("/deployments/{deployment_id}/trial/adjust")
def adjust_trial(
    deployment_id: int,
    payload: CanaryAdjustRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        _, deployment = _deployment_for_actor(db, current_user, deployment_id)
        if not deployment.canary_release_id:
            raise HTTPException(status_code=409, detail="trial_not_active")
        active = _release_or_404(db, deployment.active_release_id)
        candidate = _release_or_404(db, deployment.canary_release_id)
        revision = _apply_canary_state(
            db,
            current_user=current_user,
            deployment=deployment,
            active=active,
            canary=candidate,
            percent=payload.percent,
            action="canary_adjust",
            reason=payload.reason,
        )
    return {"ok": True, "revision": revision.revision, "deployment": _deployment_snapshot(deployment)}


@router.post("/deployments/{deployment_id}/trial/pause")
def pause_trial(
    deployment_id: int,
    payload: CanaryActionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        _, deployment = _deployment_for_actor(db, current_user, deployment_id)
        if not deployment.canary_release_id:
            raise HTTPException(status_code=409, detail="trial_not_active")
        active = _release_or_404(db, deployment.active_release_id)
        candidate = _release_or_404(db, deployment.canary_release_id)
        revision = _apply_canary_state(
            db,
            current_user=current_user,
            deployment=deployment,
            active=active,
            canary=candidate,
            percent=0,
            action="canary_pause",
            reason=payload.reason,
        )
    return {"ok": True, "revision": revision.revision, "deployment": _deployment_snapshot(deployment)}


@router.post("/deployments/{deployment_id}/trial/promote")
def promote_trial(
    deployment_id: int,
    payload: CanaryActionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        _, deployment = _deployment_for_actor(db, current_user, deployment_id)
        if not deployment.canary_release_id:
            raise HTTPException(status_code=409, detail="trial_not_active")
        candidate = _release_or_404(db, deployment.canary_release_id)
        active = _release_or_404(db, deployment.active_release_id)
        _assert_same_definition(active, candidate)
        revision = _apply_canary_state(
            db,
            current_user=current_user,
            deployment=deployment,
            active=candidate,
            canary=None,
            percent=0,
            action="canary_promote",
            reason=payload.reason,
        )
    return {"ok": True, "revision": revision.revision, "deployment": _deployment_snapshot(deployment)}
