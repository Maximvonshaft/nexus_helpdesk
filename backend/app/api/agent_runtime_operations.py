from __future__ import annotations

import asyncio

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
from ..services.permissions import (
    CAP_AI_CONFIG_READ,
    CAP_RUNTIME_MANAGE,
    ensure_capability,
)
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


@router.post("/integrations/mcp/doctor")
async def run_mcp_doctor(
    payload: MCPDoctorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Diagnose one MCP integration from the exact deployed Agent Release.

    The Doctor may read remote capability metadata using configured credentials,
    so it requires both configuration visibility and runtime authority. It never
    publishes discovered tools, changes a Release, or executes a business Tool.
    """

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
    row = (
        db.query(AgentRun)
        .filter(AgentRun.id == run_id, AgentRun.tenant_key == tenant)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    snapshot = (
        db.query(AgentRunSnapshot)
        .filter(AgentRunSnapshot.request_id == row.request_id)
        .one_or_none()
    )
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
    run = (
        db.query(AgentRun)
        .filter(AgentRun.id == run_id, AgentRun.tenant_key == tenant)
        .one_or_none()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
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
