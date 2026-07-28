from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.case_scenario_service import (
    current_case_scenario_assignment,
    reclassify_case_scenario,
    serialize_case_scenario_assignment,
)
from ..services.permissions import ensure_can_escalate, ensure_ticket_visible
from ..services.ticket_service import get_ticket_or_404
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/tickets", tags=["ticket-scenarios"])


class CaseScenarioReclassifyRequest(BaseModel):
    scenario_key: str = Field(min_length=2, max_length=160)
    reason: str = Field(min_length=3, max_length=2000)


@router.get("/{ticket_id}/scenario")
def get_case_scenario_assignment(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    row = current_case_scenario_assignment(db, ticket_id=ticket.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "case_scenario_assignment_missing"},
        )
    return serialize_case_scenario_assignment(row)


@router.post("/{ticket_id}/scenario/reclassify")
def reclassify_case_scenario_assignment(
    ticket_id: int,
    payload: CaseScenarioReclassifyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_escalate(current_user, db)
    with managed_session(db):
        row = reclassify_case_scenario(
            db,
            ticket=ticket,
            scenario_key=payload.scenario_key,
            reason=payload.reason,
            actor_id=current_user.id,
        )
        db.flush()
    return serialize_case_scenario_assignment(row)
