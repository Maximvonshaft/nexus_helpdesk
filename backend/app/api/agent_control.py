from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AIConfigResource, Customer, Tenant
from ..models_agent_control import AgentDefinition, AgentDeployment, AgentRelease, AgentRunSnapshot
from ..models_osr import ToolExecutionPolicyRecord
from ..services import persona_service
from ..services.agent_control_config import CANONICAL_AGENT_CONFIG_TYPES, safe_resource_payload
from ..services.agent_release_service import (
    RELEASE_SCHEMA,
    activate_deployment,
    authoritative_tenant_key,
    canonical_scope_key,
    create_release,
    resolve_agent_release,
    validate_release_manifest,
)
from ..services.agent_runtime.playbook_registry import prompt_playbook_catalog
from ..services.agent_runtime.runtime import run_agent_with_db
from ..services.agent_runtime.tool_adapter import executable_tool_names
from ..services.agent_tool_contracts import bootstrap_agent_tool_contracts
from ..services.ai_runtime.schemas import RuntimeAIProviderRequest
from ..services.ai_runtime_context import build_agent_context
from ..services.integration_runtime import execute_integration_operation, list_integration_catalog
from ..services.permissions import (
    ensure_can_manage_ai_configs,
    ensure_can_manage_runtime,
    ensure_can_read_ai_configs,
)
from ..services.webchat_ai_decision_runtime.tool_registry import (
    get_tool_contract,
    safe_registry_summary,
)
from ..unit_of_work import managed_session
from .deps import get_current_user

bootstrap_agent_tool_contracts()
router = APIRouter(prefix="/api/agent-control", tags=["agent-control"])
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,119}$")


class AgentDefinitionCreate(BaseModel):
    tenant_key: str | None = Field(default=None, max_length=80)
    definition_key: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    purpose: str | None = Field(default=None, max_length=4000)
    owner_team_id: int | None = None
    draft_manifest: dict[str, Any]

    @field_validator("definition_key")
    @classmethod
    def validate_definition_key(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _KEY_RE.fullmatch(cleaned):
            raise ValueError("definition_key_invalid")
        return cleaned


class AgentDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    purpose: str | None = Field(default=None, max_length=4000)
    owner_team_id: int | None = None
    is_active: bool | None = None
    draft_manifest: dict[str, Any] | None = None


class AgentDeploymentRequest(BaseModel):
    tenant_key: str | None = Field(default=None, max_length=80)
    environment: str = Field(default="production", max_length=24)
    release_id: int = Field(gt=0)
    canary_release_id: int | None = Field(default=None, gt=0)
    canary_percent: int = Field(default=0, ge=0, le=100)
    market_id: int | None = None
    channel: str | None = Field(default=None, max_length=40)
    language: str | None = Field(default=None, max_length=24)
    case_type: str | None = Field(default=None, max_length=80)


class AgentResolveRequest(BaseModel):
    tenant_key: str | None = Field(default=None, max_length=80)
    environment: str = Field(default="production", max_length=24)
    market_id: int | None = None
    channel: str = Field(default="webchat", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=24)
    case_type: str | None = Field(default=None, max_length=80)
    cohort_key: str = Field(default="preview", min_length=1, max_length=160)


class PlaygroundRequest(AgentResolveRequest):
    body: str = Field(min_length=1, max_length=4000)
    customer_id: int | None = None
    execute_model: bool = False


class IntegrationTestRequest(BaseModel):
    integration_key: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    market_id: int | None = None
    channel: str = Field(default="webchat", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=24)


@router.get("/snapshot")
def agent_control_snapshot(
    tenant_key: str | None = Query(default=None, max_length=80),
    environment: str = "production",
    market_id: int | None = None,
    channel: str = "webchat",
    language: str | None = None,
    case_type: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    definitions = (
        db.query(AgentDefinition)
        .filter(AgentDefinition.tenant_key == tenant)
        .order_by(AgentDefinition.name.asc(), AgentDefinition.id.asc())
        .all()
    )
    definition_ids = [row.id for row in definitions]
    releases = (
        db.query(AgentRelease)
        .filter(AgentRelease.definition_id.in_(definition_ids))
        .order_by(AgentRelease.definition_id.asc(), AgentRelease.version.desc())
        .all()
        if definition_ids
        else []
    )
    deployments = (
        db.query(AgentDeployment)
        .filter(AgentDeployment.tenant_key == tenant)
        .order_by(AgentDeployment.environment.asc(), AgentDeployment.scope_key.asc())
        .all()
    )
    resolved = resolve_agent_release(
        db,
        tenant_key=tenant,
        environment=environment,
        market_id=market_id,
        channel=channel,
        language=language,
        case_type=case_type,
        cohort_key="control-plane-snapshot",
    )
    resources = (
        db.query(AIConfigResource)
        .filter(AIConfigResource.config_type.in_(CANONICAL_AGENT_CONFIG_TYPES))
        .order_by(AIConfigResource.config_type.asc(), AIConfigResource.name.asc())
        .all()
    )
    profiles, profile_total = persona_service.list_profiles(
        db,
        market_id=None,
        channel=None,
        language=None,
        is_active=None,
        q=None,
        limit=200,
        offset=0,
    )
    policies = (
        db.query(ToolExecutionPolicyRecord)
        .order_by(
            ToolExecutionPolicyRecord.tool_name.asc(),
            ToolExecutionPolicyRecord.country_code.asc(),
            ToolExecutionPolicyRecord.channel.asc(),
        )
        .all()
    )
    executable = set(executable_tool_names())
    tools = [
        {**item, "executable": str(item.get("name")) in executable}
        for item in safe_registry_summary()
    ]
    return {
        "generated_at": time.time(),
        "tenant_key": tenant,
        "scope": {
            "environment": environment,
            "market_id": market_id,
            "channel": channel,
            "language": language,
            "case_type": case_type,
        },
        "definitions": [_definition_payload(row) for row in definitions],
        "releases": [_release_payload(row) for row in releases],
        "deployments": [_deployment_payload(row) for row in deployments],
        "resolved_agent": resolved.snapshot,
        "resolved_agent_digest": resolved.digest,
        "personas": [_persona_payload(row) for row in profiles],
        "persona_total": profile_total,
        "resources": [safe_resource_payload(row) for row in resources],
        "resolved_playbooks": prompt_playbook_catalog(
            db,
            market_id=market_id,
            channel=channel,
            language=language,
            available_tools=executable,
            release_snapshot=resolved.snapshot,
        ),
        "tools": tools,
        "tool_policies": [_tool_policy(row) for row in policies],
        "integrations": list_integration_catalog(
            db,
            market_id=market_id,
            channel=channel,
            language=language,
            release_snapshot=resolved.snapshot,
        ),
        "capabilities": {
            "can_manage": _can_manage(current_user, db),
            "can_deploy": _can_deploy(current_user, db),
            "playground_model_execution": _can_manage(current_user, db),
        },
    }


@router.post("/definitions")
def create_agent_definition(
    payload: AgentDefinitionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    normalized, _evidence = validate_release_manifest(db, payload.draft_manifest)
    if (
        db.query(AgentDefinition)
        .filter(
            AgentDefinition.tenant_key == tenant,
            AgentDefinition.definition_key == payload.definition_key,
        )
        .first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="agent_definition_key_exists")
    with managed_session(db):
        row = AgentDefinition(
            tenant_key=tenant,
            definition_key=payload.definition_key,
            name=payload.name.strip(),
            purpose=_clean(payload.purpose),
            owner_team_id=payload.owner_team_id,
            is_active=True,
            draft_manifest_json=normalized,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(row)
        db.flush()
    db.refresh(row)
    return _definition_payload(row)


@router.put("/definitions/{definition_id}")
def update_agent_definition(
    definition_id: int,
    payload: AgentDefinitionUpdate,
    tenant_key: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    row = _definition_or_404(db, definition_id, tenant)
    values = payload.model_dump(exclude_unset=True)
    if "draft_manifest" in values:
        normalized, _evidence = validate_release_manifest(db, values.pop("draft_manifest"))
        values["draft_manifest_json"] = normalized
    if "name" in values:
        values["name"] = str(values["name"]).strip()
    if "purpose" in values:
        values["purpose"] = _clean(values["purpose"])
    with managed_session(db):
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_by = current_user.id
        db.flush()
    db.refresh(row)
    return _definition_payload(row)


@router.post("/definitions/{definition_id}/releases")
def release_agent_definition(
    definition_id: int,
    tenant_key: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    definition = _definition_or_404(db, definition_id, tenant)
    with managed_session(db):
        release = create_release(db, definition=definition, actor_id=current_user.id)
    db.refresh(release)
    return _release_payload(release)


@router.put("/deployments")
def deploy_agent_release(
    payload: AgentDeploymentRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    release = db.get(AgentRelease, payload.release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="agent_release_not_found")
    canary = db.get(AgentRelease, payload.canary_release_id) if payload.canary_release_id else None
    if payload.canary_release_id and canary is None:
        raise HTTPException(status_code=404, detail="agent_canary_release_not_found")
    with managed_session(db):
        deployment = activate_deployment(
            db,
            tenant_key=tenant,
            environment=payload.environment,
            release=release,
            canary_release=canary,
            canary_percent=payload.canary_percent,
            actor_id=current_user.id,
            market_id=payload.market_id,
            channel=payload.channel,
            language=payload.language,
            case_type=payload.case_type,
        )
    db.refresh(deployment)
    return _deployment_payload(deployment)


@router.post("/resolve")
def resolve_agent_configuration(
    payload: AgentResolveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    resolved = resolve_agent_release(
        db,
        tenant_key=tenant,
        environment=payload.environment,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        case_type=payload.case_type,
        cohort_key=payload.cohort_key,
    )
    return {"digest": resolved.digest, "snapshot": resolved.snapshot}


@router.post("/playground")
async def agent_playground(
    payload: PlaygroundRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    customer = db.get(Customer, payload.customer_id) if payload.customer_id else None
    if payload.customer_id and customer is None:
        raise HTTPException(status_code=404, detail="customer_not_found")
    _ensure_customer_tenant(db, customer, tenant)
    request_id = f"playground-{current_user.id}-{time.time_ns()}"
    session_id = f"playground:{current_user.id}"
    context = build_agent_context(
        db,
        tenant_key=tenant,
        channel_key=payload.channel,
        body=payload.body,
        market_id=payload.market_id,
        language=payload.language,
        customer=customer,
        request_id=request_id,
        session_id=session_id,
        environment=payload.environment,
        case_type=payload.case_type,
    )
    read_tools = _read_only_tools()
    playbooks = prompt_playbook_catalog(
        db,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        available_tools=read_tools,
        release_snapshot=context.get("agent_release_snapshot"),
    )
    preview = {
        "agent_release": context.get("agent_release_snapshot"),
        "agent_release_digest": context.get("agent_release_digest"),
        "persona": context.get("persona_context"),
        "active_bulletins": context.get("active_bulletins"),
        "playbooks": playbooks,
        "tools": [
            get_tool_contract(name).prompt_projection()
            for name in sorted(read_tools)
            if get_tool_contract(name) is not None
        ],
        "model_executed": False,
    }
    if not payload.execute_model:
        db.rollback()
        return preview
    ensure_can_manage_ai_configs(current_user, db)
    execution_context = dict(context.get("agent_execution_context") or {})
    permissions = sorted(
        {
            permission
            for name in read_tools
            if (contract := get_tool_contract(name)) is not None
            for permission in contract.required_permissions
        }
    )
    execution_context.update(
        {
            "granted_permissions": permissions,
            "actor_capabilities": permissions,
            "customer_confirmation_granted": False,
            "human_confirmation_granted": False,
        }
    )
    runtime_context = {
        **context,
        "agent_allowed_tools": sorted(read_tools),
        "agent_execution_context": execution_context,
        "playground": True,
    }
    result = await run_agent_with_db(
        db,
        request=RuntimeAIProviderRequest(
            tenant_key=tenant,
            channel_key=payload.channel,
            session_id=session_id,
            body=payload.body,
            recent_context=context.get("recent_conversation") or [],
            request_id=request_id,
            market_id=payload.market_id,
            language=payload.language,
            metadata=runtime_context,
        ),
    )
    db.rollback()
    preview.update(
        {
            "model_executed": True,
            "reply": result.reply,
            "reply_source": result.reply_source,
            "intent": result.intent,
            "handoff_required": result.handoff_required,
            "tool_calls": result.tool_calls,
            "runtime_trace": result.raw_payload_safe_summary,
            "error_code": result.error_code,
        }
    )
    return preview


@router.post("/integrations/test")
def test_integration(
    payload: IntegrationTestRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    catalog = list_integration_catalog(
        db,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
    )
    selected = next(
        (item for item in catalog if item["resource_key"] == payload.integration_key),
        None,
    )
    operation = next(
        (
            item
            for item in (selected or {}).get("operations", [])
            if item.get("key") == payload.operation
        ),
        None,
    )
    if operation is None:
        raise HTTPException(status_code=404, detail="integration_operation_not_found")
    expected_write = operation.get("mode") == "write"
    result = execute_integration_operation(
        db,
        integration_key=payload.integration_key,
        operation=payload.operation,
        arguments=payload.arguments,
        expected_write=expected_write,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        dry_run=expected_write,
    )
    return result.safe_summary()


@router.get("/runs")
def list_agent_run_snapshots(
    tenant_key: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    rows = (
        db.query(AgentRunSnapshot)
        .filter(AgentRunSnapshot.tenant_key == tenant)
        .order_by(AgentRunSnapshot.created_at.desc(), AgentRunSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "request_id": row.request_id,
            "session_id": row.session_id,
            "deployment_id": row.deployment_id,
            "release_id": row.release_id,
            "snapshot_sha256": row.snapshot_sha256,
            "source": row.source,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def _definition_or_404(db: Session, definition_id: int, tenant_key: str) -> AgentDefinition:
    row = (
        db.query(AgentDefinition)
        .filter(
            AgentDefinition.id == definition_id,
            AgentDefinition.tenant_key == tenant_key,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent_definition_not_found")
    return row


def _ensure_customer_tenant(db: Session, customer: Customer | None, tenant_key: str) -> None:
    if customer is None:
        return
    if tenant_key == "default" and customer.tenant_id is None:
        return
    tenant = db.get(Tenant, customer.tenant_id) if customer.tenant_id else None
    if tenant is None or tenant.tenant_key != tenant_key:
        raise HTTPException(status_code=403, detail="cross_tenant_customer_forbidden")


def _definition_payload(row: AgentDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_key": row.tenant_key,
        "definition_key": row.definition_key,
        "name": row.name,
        "purpose": row.purpose,
        "owner_team_id": row.owner_team_id,
        "is_active": row.is_active,
        "draft_manifest": row.draft_manifest_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _release_payload(row: AgentRelease) -> dict[str, Any]:
    return {
        "id": row.id,
        "definition_id": row.definition_id,
        "version": row.version,
        "status": row.status,
        "manifest": row.manifest_json,
        "manifest_sha256": row.manifest_sha256,
        "validation": row.validation_json,
        "created_at": row.created_at,
        "approved_at": row.approved_at,
    }


def _deployment_payload(row: AgentDeployment) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_key": row.tenant_key,
        "environment": row.environment,
        "scope_key": row.scope_key,
        "market_id": row.market_id,
        "channel": row.channel,
        "language": row.language,
        "case_type": row.case_type,
        "active_release_id": row.active_release_id,
        "canary_release_id": row.canary_release_id,
        "canary_percent": row.canary_percent,
        "is_active": row.is_active,
        "activated_at": row.activated_at,
        "updated_at": row.updated_at,
    }


def _persona_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "profile_key": row.profile_key,
        "name": row.name,
        "description": row.description,
        "market_id": row.market_id,
        "channel": row.channel,
        "language": row.language,
        "is_active": row.is_active,
        "draft_summary": row.draft_summary,
        "draft_content_json": row.draft_content_json or {},
        "published_summary": row.published_summary,
        "published_content_json": row.published_content_json or {},
        "published_version": row.published_version,
        "published_at": row.published_at,
        "updated_at": row.updated_at,
    }


def _read_only_tools() -> set[str]:
    return {
        name
        for name in executable_tool_names()
        if (contract := get_tool_contract(name)) is not None and contract.is_read_tool
    }


def _tool_policy(row: ToolExecutionPolicyRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "tool_name": row.tool_name,
        "country_code": row.country_code,
        "channel": row.channel,
        "enabled": row.enabled,
        "ai_auto_executable": row.ai_auto_executable,
        "risk_level": row.risk_level,
        "requires_tracking_number": row.requires_tracking_number,
        "requires_contact": row.requires_contact,
        "requires_customer_confirmation": row.requires_customer_confirmation,
        "requires_human_confirmation": row.requires_human_confirmation,
        "allowed_channels_json": row.allowed_channels_json,
        "allowed_countries_json": row.allowed_countries_json,
        "audit_level": row.audit_level,
        "updated_at": row.updated_at,
    }


def _can_manage(current_user, db: Session) -> bool:
    try:
        ensure_can_manage_ai_configs(current_user, db)
    except HTTPException:
        return False
    return True


def _can_deploy(current_user, db: Session) -> bool:
    try:
        ensure_can_manage_runtime(current_user, db)
    except HTTPException:
        return False
    return True


def _clean(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").split())
    return cleaned or None
