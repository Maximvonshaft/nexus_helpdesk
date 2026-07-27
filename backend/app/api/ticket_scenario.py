from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import TicketStatus
from ..models_scenario_assignment import TicketScenarioAssignment
from ..services.permissions import ensure_can_assign, ensure_ticket_visible
from ..services.scenario_assignment_service import (
    TicketScenarioAssignmentError,
    assign_ticket_scenario,
    get_assigned_scenario,
)
from ..services.ticket_service import get_ticket_or_404
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/tickets", tags=["ticket-scenario"])


class TicketScenarioCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_key: str = Field(min_length=2, max_length=160)
    reason: str = Field(min_length=5, max_length=1000)


def _serialize(row: TicketScenarioAssignment, *, review_overdue: bool) -> dict:
    return {
        "ticket_id": row.ticket_id,
        "tenant_id": row.tenant_id,
        "scenario_key": row.scenario_key,
        "assignment_revision": row.assignment_revision,
        "catalog_version": row.catalog_version,
        "catalog_sha256": row.catalog_sha256,
        "definition_sha256": row.definition_sha256,
        "assignment_source": row.assignment_source,
        "assignment_reason": row.assignment_reason,
        "assigned_by": row.assigned_by,
        "assigned_at": row.assigned_at,
        "review_overdue": review_overdue,
    }


@router.get("/{ticket_id}/scenario")
def get_ticket_scenario(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    try:
        assigned = get_assigned_scenario(db, ticket=ticket, required=True)
    except TicketScenarioAssignmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    assert assigned is not None
    return _serialize(
        assigned.assignment,
        review_overdue=assigned.policy.review_overdue,
    )


@router.post("/{ticket_id}/scenario")
def assign_or_reclassify_ticket_scenario(
    ticket_id: int,
    payload: TicketScenarioCommand,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_assign(current_user, db)
    if ticket.status in {
        TicketStatus.resolved,
        TicketStatus.closed,
        TicketStatus.canceled,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="scenario_reclassification_requires_reopen",
        )
    try:
        with managed_session(db):
            row = assign_ticket_scenario(
                db,
                ticket=ticket,
                scenario_key=payload.scenario_key,
                actor_id=current_user.id,
                source="operator_command",
                reason=payload.reason,
                allow_reclassification=True,
            )
        assigned = get_assigned_scenario(db, ticket=ticket, required=True)
    except TicketScenarioAssignmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    assert assigned is not None
    return _serialize(row, review_overdue=assigned.policy.review_overdue)
