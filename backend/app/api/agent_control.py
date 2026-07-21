from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AIConfigResource, Customer
from ..models_osr import ToolExecutionPolicyRecord
from ..services import persona_service
from ..services.agent_control_config import (
    CANONICAL_AGENT_CONFIG_TYPES,
    safe_resource_payload,
)
from ..services.agent_runtime.playbook_registry import prompt_playbook_catalog
from ..services.agent_runtime.runtime import run_agent_with_db
from ..services.agent_runtime.tool_adapter import executable_tool_names
from ..services.agent_tool_contracts import bootstrap_agent_tool_contracts
from ..services.ai_runtime.schemas import RuntimeAIProviderRequest
from ..services.ai_runtime_context import build_agent_context
from ..services.customer_memory_service import (
    deactivate_customer_memory,
    forget_customer_memory,
    list_customer_memory,
    resolve_memory_policy,
    upsert_customer_memory,
)
from ..services.integration_runtime import (
    execute_integration_operation,
    list_integration_catalog,
)
from ..services.permissions import (
    ensure_can_manage_ai_configs,
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


class PlaygroundRequest(BaseModel):
    tenant_key: str = Field(default="default", min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=4000)
    market_id: int | None = None
    channel: str = Field(default="webchat", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=24)
    customer_id: int | None = None
    execute_model: bool = False


class IntegrationTestRequest(BaseModel):
    integration_key: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    market_id: int | None = None
    channel: str = Field(default="webchat", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=24)


class CustomerMemoryUpsertRequest(BaseModel):
    tenant_key: str = Field(default="default", min_length=1, max_length=80)
    memory_key: str = Field(min_length=1, max_length=120)
    value_text: str = Field(min_length=1, max_length=2000)
    consent_basis: str | None = Field(default=None, max_length=80)
    source_type: str = Field(default="operator", max_length=40)
    source_reference: str | None = Field(default=None, max_length=200)
    confidence: float = Field(default=1.0, ge=0, le=1)
    sensitivity: str = Field(default="standard", max_length=20)
    market_id: int | None = None
    channel: str | None = Field(default=None, max_length=40)
    language: str | None = Field(default=None, max_length=24)


class ForgetMemoryRequest(BaseModel):
    tenant_key: str = Field(default="default", min_length=1, max_length=80)


@router.get("/snapshot")
def agent_control_snapshot(
    tenant_key: str = Query(default="default", min_length=1, max_length=80),
    market_id: int | None = None,
    channel: str = "webchat",
    language: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
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
    playbooks = prompt_playbook_catalog(
        db,
        market_id=market_id,
        channel=channel,
        language=language,
        available_tools=executable,
    )
    return {
        "generated_at": time.time(),
        "tenant_key": tenant_key,
        "scope": {"market_id": market_id, "channel": channel, "language": language},
        "personas": [
            {
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
            for row in profiles
        ],
        "persona_total": profile_total,
        "resources": [safe_resource_payload(row) for row in resources],
        "resolved_playbooks": playbooks,
        "tools": tools,
        "tool_policies": [_tool_policy(row) for row in policies],
        "integrations": list_integration_catalog(
            db,
            market_id=market_id,
            channel=channel,
            language=language,
        ),
        "memory_policy": resolve_memory_policy(
            db,
            market_id=market_id,
            channel=channel,
            language=language,
        ),
        "capabilities": {
            "can_manage": _can_manage(current_user, db),
            "playground_model_execution": _can_manage(current_user, db),
        },
    }


@router.post("/playground")
async def agent_playground(
    payload: PlaygroundRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    customer = db.get(Customer, payload.customer_id) if payload.customer_id else None
    if payload.customer_id and customer is None:
        raise HTTPException(status_code=404, detail="customer_not_found")
    context = build_agent_context(
        db,
        tenant_key=payload.tenant_key,
        channel_key=payload.channel,
        body=payload.body,
        market_id=payload.market_id,
        language=payload.language,
        customer=customer,
    )
    read_tools = _read_only_tools()
    playbooks = prompt_playbook_catalog(
        db,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        available_tools=read_tools,
    )
    preview = {
        "persona": context.get("persona_context"),
        "customer_memory": context.get("customer_memory"),
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
            tenant_key=payload.tenant_key,
            channel_key=payload.channel,
            session_id=f"playground:{current_user.id}",
            body=payload.body,
            recent_context=context.get("recent_conversation") or [],
            request_id=f"playground-{current_user.id}-{time.time_ns()}",
            market_id=payload.market_id,
            language=payload.language,
            metadata=runtime_context,
        ),
    )
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
    selected = next((item for item in catalog if item["resource_key"] == payload.integration_key), None)
    operation = next(
        (item for item in (selected or {}).get("operations", []) if item.get("key") == payload.operation),
        None,
    )
    if operation is None:
        raise HTTPException(status_code=404, detail="integration_operation_not_found")
    expected_write = str(operation.get("method") or "GET").upper() != "GET"
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


@router.get("/customers/{customer_id}/memory")
def get_customer_memory(
    customer_id: int,
    tenant_key: str = Query(default="default", min_length=1, max_length=80),
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    return {
        "customer_id": customer_id,
        "facts": list_customer_memory(
            db,
            tenant_key=tenant_key,
            customer_id=customer_id,
            include_inactive=include_inactive,
        ),
    }


@router.put("/customers/{customer_id}/memory")
def put_customer_memory(
    customer_id: int,
    payload: CustomerMemoryUpsertRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = upsert_customer_memory(
            db,
            tenant_key=payload.tenant_key,
            customer_id=customer_id,
            memory_key=payload.memory_key,
            value_text=payload.value_text,
            actor_id=current_user.id,
            consent_basis=payload.consent_basis,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
            confidence=payload.confidence,
            sensitivity=payload.sensitivity,
            market_id=payload.market_id,
            channel=payload.channel,
            language=payload.language,
        )
    db.refresh(row)
    return list_customer_memory(
        db,
        tenant_key=payload.tenant_key,
        customer_id=customer_id,
        include_inactive=True,
    )


@router.delete("/customers/{customer_id}/memory/{memory_id}")
def delete_customer_memory(
    customer_id: int,
    memory_id: int,
    tenant_key: str = Query(default="default", min_length=1, max_length=80),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = deactivate_customer_memory(
            db,
            tenant_key=tenant_key,
            customer_id=customer_id,
            memory_id=memory_id,
            actor_id=current_user.id,
        )
    db.refresh(row)
    return {"ok": True, "memory_id": row.id, "is_active": row.is_active}


@router.post("/customers/{customer_id}/memory/forget")
def forget_customer(
    customer_id: int,
    payload: ForgetMemoryRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        deleted = forget_customer_memory(
            db,
            tenant_key=payload.tenant_key,
            customer_id=customer_id,
            actor_id=current_user.id,
        )
    return {"ok": True, "customer_id": customer_id, "deleted": deleted}


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
