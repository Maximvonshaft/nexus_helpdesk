from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Ticket
from ..services.permissions import ensure_ticket_visible
from ..services.ticket_closure_readiness import (
    build_closure_snapshot,
    record_closure_evidence,
)
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/tickets", tags=["tickets", "safe-closure"])


class TicketClosureEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "customer_input", "action", "outcome", "notification"]
    key: str = Field(min_length=1, max_length=160)
    state: Literal["verified", "completed", "waived", "failed"]
    source_kind: Literal[
        "tracking",
        "provider_receipt",
        "operations_dispatch",
        "customer_confirmation",
        "policy_decision",
        "operator_observation",
    ]
    source_ref: str = Field(min_length=1, max_length=200)
    source_revision: str = Field(min_length=1, max_length=160)
    observed_at: datetime
    note: str | None = Field(default=None, max_length=500)


def _ticket(db: Session, ticket_id: int, current_user) -> Ticket:
    row = db.get(Ticket, ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    ensure_ticket_visible(current_user, row, db)
    return row


@router.get("/{ticket_id}/closure-readiness")
def ticket_closure_readiness(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ticket = _ticket(db, ticket_id, current_user)
    return build_closure_snapshot(db, ticket).receipt


@router.post("/{ticket_id}/closure-evidence")
def add_ticket_closure_evidence(
    ticket_id: int,
    payload: TicketClosureEvidenceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ticket = _ticket(db, ticket_id, current_user)
    try:
        with managed_session(db):
            event = record_closure_evidence(
                db,
                ticket=ticket,
                current_user=current_user,
                kind=payload.kind,
                key=payload.key,
                state=payload.state,
                source_kind=payload.source_kind,
                source_ref=payload.source_ref,
                source_revision=payload.source_revision,
                observed_at=payload.observed_at,
                note=payload.note,
            )
            db.flush()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot = build_closure_snapshot(db, ticket)
    event_payload = json.loads(event.payload_json)
    return {
        "schema": "nexus.ticket-closure-evidence-result.v1",
        "event_id": event.id,
        "evidence_sha256": event_payload["evidence_sha256"],
        "closure": snapshot.receipt,
    }
