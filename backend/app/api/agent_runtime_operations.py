from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..models_agent_control import AgentRun, AgentRunEvent, AgentRunSnapshot
from ..services.agent_integration_service import doctor_mcp_integration
from ..services.agent_release_service import (
    authoritative_tenant_key,
    resolve_agent_release,
)
from ..services.agent_runtime.run_events import (
    agent_event_payload,
    agent_run_payload,
)
from ..services.agent_runtime.runtime import run_agent_with_db
from ..services.agent_runtime.specialist_service import run_read_only_specialists
from ..services.agent_runtime.tool_adapter import executable_tool_names
from ..services.ai_runtime.schemas import RuntimeAIProviderRequest
from ..services.ai_runtime_context import build_agent_context
from ..services.permissions import (
    CAP_AI_CONFIG_READ,
    CAP_RUNTIME_MANAGE,
    ensure_capability,
)
from ..services.webchat_ai_decision_runtime.tool_registry import get_tool_contract
from .deps import get_current_user

router = APIRouter(prefix="/api/agent-control", tags=["agent-control"])


class MCPDoctorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_key: str | None = Field(default=None, max_length=80)
    integration_key: str = Field(min_length=1, max_length=160)
    environment: str = Field(default="production", max_length=24)
    market_id: int | None = Field(default=None, ge=1)
    channel: str | None = Field(default=None, max_length=40)
    language: str | None = Field(default=None, max_length=24)
    case_type: str | None = Field(default=None, max_length=80)
    cohort_key: str = Field(
        default="operator-mcp-doctor", min_length=1, max_length=160
    )


class SpecialistReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_key: str | None = Field(default=None, max_length=80)
    specialists: list[str] = Field(min_length=1, max_length=3)


class AgentRunForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_key: str | None = Field(default=None, max_length=80)
    body: str = Field(min_length=1, max_length=4000)
    fork_kind: Literal["playground", "replay"] = "replay"
    environment: Literal["test", "staging", "production"] = "production"
    market_id: int | None = Field(default=None, ge=1)
    channel: str = Field(default="webchat", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=24)
    case_type: str | None = Field(default=None, max_length=80)
    cohort_key: str = Field(default="operator-fork", min_length=1, max_length=80)
    specialists: list[str] = Field(default_factory=list, max_length=3)
    execute_model: bool = True


@router.post("/integrations/mcp/doctor")
async def run_mcp_doctor(
    payload: MCPDoctorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Diagnose one MCP integration from the exact deployed Agent Release."""

    ensure_capability(
        current_user,
        CAP_AI_CONFIG_READ,
        db,
        message="Agent configuration read capability required",
    )
    ensure_capability(
        current_user,
        CAP_RUNTIME_MANAGE,
        db,
        message="Runtime management capability required",
    )
    tenant_key = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    resolved = resolve_agent_release(
        db,
        tenant_key=tenant_key,
        environment=payload.environment,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        case_type=payload.case_type,
        cohort_key=payload.cohort_key,
    )
    report = await asyncio.to_thread(
        doctor_mcp_integration,
        None,
        integration_key=payload.integration_key,
        release_snapshot=resolved.snapshot,
    )
    return {
        **report.safe_summary(),
        "tenant_key": tenant_key,
        "agent_release_id": resolved.release.id,
        "agent_release_version": resolved.release.version,
        "agent_release_digest": resolved.release.manifest_sha256,
        "deployment_id": resolved.deployment.id,
    }


@router.get("/runs/lifecycle")
def list_agent_runs(
    tenant_key: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_capability(
        current_user,
        CAP_AI_CONFIG_READ,
        db,
        message="Agent configuration read capability required",
    )
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    query = db.query(AgentRun).filter(AgentRun.tenant_key == tenant)
    if status:
        query = query.filter(AgentRun.status == status.strip().lower())
    rows = (
        query.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(limit)
        .all()
    )
    return [agent_run_payload(row) for row in rows]


@router.get("/runs/{run_id}")
def get_agent_run(
    run_id: int,
    tenant_key: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_capability(
        current_user,
        CAP_AI_CONFIG_READ,
        db,
        message="Agent configuration read capability required",
    )
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    row = _run_or_404(db, run_id=run_id, tenant_key=tenant)
    snapshot = _snapshot_for_run(db, row)
    return {
        **agent_run_payload(row),
        "snapshot_evidence": (
            {
                "id": snapshot.id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "source": snapshot.source,
                "created_at": snapshot.created_at,
            }
            if snapshot is not None
            else None
        ),
    }


@router.get("/runs/{run_id}/events")
def list_agent_run_events(
    run_id: int,
    tenant_key: str | None = Query(default=None, max_length=80),
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_capability(
        current_user,
        CAP_AI_CONFIG_READ,
        db,
        message="Agent configuration read capability required",
    )
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=tenant_key,
        allow_platform_default=True,
    )
    run = _run_or_404(db, run_id=run_id, tenant_key=tenant)
    events = (
        db.query(AgentRunEvent)
        .filter(
            AgentRunEvent.run_id == run.id,
            AgentRunEvent.sequence > after_sequence,
        )
        .order_by(AgentRunEvent.sequence.asc())
        .limit(limit)
        .all()
    )
    return {
        "run": agent_run_payload(run),
        "events": [agent_event_payload(row) for row in events],
        "last_sequence": events[-1].sequence if events else after_sequence,
    }


@router.post("/runs/{run_id}/specialists")
def review_agent_run_with_specialists(
    run_id: int,
    payload: SpecialistReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Produce read-only evidence reviews from content-safe persisted facts."""

    ensure_capability(
        current_user,
        CAP_AI_CONFIG_READ,
        db,
        message="Agent configuration read capability required",
    )
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    parent = _run_or_404(db, run_id=run_id, tenant_key=tenant)
    try:
        results = run_read_only_specialists(
            db,
            parent_run=parent,
            specialists=payload.specialists,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "parent_run": agent_run_payload(parent),
        "specialists": results,
        "read_only": True,
        "customer_visible": False,
    }


@router.post("/runs/{run_id}/fork")
async def fork_agent_run(
    run_id: int,
    payload: AgentRunForkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run a read-only fork only when the original immutable snapshot matches."""

    ensure_capability(
        current_user,
        CAP_AI_CONFIG_READ,
        db,
        message="Agent configuration read capability required",
    )
    if payload.execute_model:
        ensure_capability(
            current_user,
            CAP_RUNTIME_MANAGE,
            db,
            message="Runtime management capability required",
        )
    tenant = authoritative_tenant_key(
        db,
        current_user,
        requested=payload.tenant_key,
        allow_platform_default=True,
    )
    parent = _run_or_404(db, run_id=run_id, tenant_key=tenant)
    if parent.status == "running":
        raise HTTPException(status_code=409, detail="agent_parent_run_not_terminal")
    snapshot = _snapshot_for_run(db, parent)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="agent_run_snapshot_unavailable")

    fork_session_id = (
        f"{payload.fork_kind}:{parent.id}:{current_user.id}:"
        f"{payload.cohort_key}:{time.time_ns()}"
    )[:160]
    request_id = f"agent-fork-{parent.id}-{current_user.id}-{time.time_ns()}"[:160]
    context = build_agent_context(
        db,
        tenant_key=tenant,
        channel_key=payload.channel,
        body=payload.body,
        market_id=payload.market_id,
        language=payload.language,
        request_id=request_id,
        session_id=fork_session_id,
        environment=payload.environment,
        case_type=payload.case_type,
    )
    resolved_digest = str(context.get("agent_release_digest") or "")
    if resolved_digest != snapshot.snapshot_sha256:
        raise HTTPException(
            status_code=409,
            detail="agent_fork_exact_release_not_resolved",
        )

    try:
        specialist_results = run_read_only_specialists(
            db,
            parent_run=parent,
            specialists=payload.specialists,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    read_tools = _read_only_tools()
    permissions = sorted(
        {
            permission
            for name in read_tools
            if (contract := get_tool_contract(name)) is not None
            for permission in contract.required_permissions
        }
    )
    execution_context = dict(context.get("agent_execution_context") or {})
    execution_context.update(
        {
            "granted_permissions": permissions,
            "actor_capabilities": permissions,
            "customer_confirmation_granted": False,
            "human_confirmation_granted": False,
        }
    )
    channel_context = dict(context.get("channel_context") or {})
    if specialist_results:
        channel_context["specialist_evidence"] = specialist_results
    runtime_context = {
        **context,
        "channel_context": channel_context,
        "agent_allowed_tools": sorted(read_tools),
        "agent_execution_context": execution_context,
        "agent_release_digest": snapshot.snapshot_sha256,
        "agent_parent_run_id": parent.id,
        "agent_fork_kind": payload.fork_kind,
        "agent_trace_id": parent.trace_id,
        "agent_environment": payload.environment,
        "operator_fork": True,
    }
    preview: dict[str, Any] = {
        "parent_run": agent_run_payload(parent),
        "snapshot_id": snapshot.id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "resolved_digest": resolved_digest,
        "fork_kind": payload.fork_kind,
        "read_tools": sorted(read_tools),
        "specialists": specialist_results,
        "model_executed": False,
    }
    if not payload.execute_model:
        return preview

    result = await run_agent_with_db(
        db,
        request=RuntimeAIProviderRequest(
            tenant_key=tenant,
            channel_key=payload.channel,
            session_id=fork_session_id,
            body=payload.body,
            recent_context=[],
            request_id=request_id,
            market_id=payload.market_id,
            language=payload.language,
            metadata=runtime_context,
        ),
    )
    preview.update(
        {
            "model_executed": True,
            "agent_run_id": (result.raw_payload_safe_summary or {}).get("agent_run_id"),
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


def _run_or_404(db: Session, *, run_id: int, tenant_key: str) -> AgentRun:
    row = (
        db.query(AgentRun)
        .filter(AgentRun.id == run_id, AgentRun.tenant_key == tenant_key)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return row


def _snapshot_for_run(db: Session, run: AgentRun) -> AgentRunSnapshot | None:
    return (
        db.query(AgentRunSnapshot)
        .filter(AgentRunSnapshot.request_id == run.request_id)
        .one_or_none()
    )


def _read_only_tools() -> set[str]:
    return {
        name
        for name in executable_tool_names()
        if (contract := get_tool_contract(name)) is not None and contract.is_read_tool
    }
