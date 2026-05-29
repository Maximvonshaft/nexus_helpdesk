from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_governance_release import (
    GovernanceReleaseAction,
    GovernanceReleaseCreate,
    GovernanceReleaseEventRead,
    GovernanceReleaseListRead,
    GovernanceReleaseRead,
)
from ..services.governance_release_service import (
    create_release_request,
    get_release_or_404,
    list_release_events,
    list_release_requests,
    transition_release,
)
from ..services.permissions import ensure_can_manage_governance_releases, ensure_can_read_governance_releases
from ..unit_of_work import managed_session
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/governance-releases", tags=["admin-governance-releases"])


def _release_out(db: Session, row) -> GovernanceReleaseRead:
    events = [GovernanceReleaseEventRead.model_validate(event) for event in list_release_events(db, row.id)]
    return GovernanceReleaseRead.model_validate(row).model_copy(update={"events": events})


@router.get("", response_model=GovernanceReleaseListRead)
def list_admin_governance_releases(
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_governance_releases(current_user, db)
    rows, total, status_counts, source_counts, risk_counts = list_release_requests(
        db,
        status_filter=status,
        source_type=source_type,
        risk_level=risk_level,
        q=q,
        limit=limit,
        offset=offset,
    )
    return GovernanceReleaseListRead(
        items=[_release_out(db, row) for row in rows],
        total=total,
        status_counts=status_counts,
        source_counts=source_counts,
        risk_counts=risk_counts,
    )


@router.post("", response_model=GovernanceReleaseRead)
def create_admin_governance_release(
    payload: GovernanceReleaseCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_governance_releases(current_user, db)
    with managed_session(db):
        row = create_release_request(db, payload, current_user)
    db.refresh(row)
    return _release_out(db, row)


@router.get("/{release_id}", response_model=GovernanceReleaseRead)
def get_admin_governance_release(
    release_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_governance_releases(current_user, db)
    row = get_release_or_404(db, release_id)
    return _release_out(db, row)


def _action(release_id: int, action: str, payload: GovernanceReleaseAction, db: Session, current_user) -> GovernanceReleaseRead:
    ensure_can_manage_governance_releases(current_user, db)
    row = get_release_or_404(db, release_id)
    with managed_session(db):
        row = transition_release(db, row, action=action, payload=payload, actor=current_user)
    db.refresh(row)
    return _release_out(db, row)


@router.post("/{release_id}/submit", response_model=GovernanceReleaseRead)
def submit_admin_governance_release(
    release_id: int,
    payload: GovernanceReleaseAction,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _action(release_id, "submit", payload, db, current_user)


@router.post("/{release_id}/approve", response_model=GovernanceReleaseRead)
def approve_admin_governance_release(
    release_id: int,
    payload: GovernanceReleaseAction,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _action(release_id, "approve", payload, db, current_user)


@router.post("/{release_id}/publish", response_model=GovernanceReleaseRead)
def publish_admin_governance_release(
    release_id: int,
    payload: GovernanceReleaseAction,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _action(release_id, "publish", payload, db, current_user)


@router.post("/{release_id}/rollback", response_model=GovernanceReleaseRead)
def rollback_admin_governance_release(
    release_id: int,
    payload: GovernanceReleaseAction,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _action(release_id, "rollback", payload, db, current_user)


@router.post("/{release_id}/reject", response_model=GovernanceReleaseRead)
def reject_admin_governance_release(
    release_id: int,
    payload: GovernanceReleaseAction,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _action(release_id, "reject", payload, db, current_user)
