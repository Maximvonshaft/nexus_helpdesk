from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.webcall_operator_workbench import build_webcall_operator_workbench
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/webcall/operator", tags=["webcall-operator"])


@router.get("/workbench")
def get_webcall_operator_workbench(
    view: str = Query(default="requested", pattern="^(requested|mine|ai_active|closed)$"),
    voice_status: str = Query(default="incoming", max_length=40),
    ticket_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return build_webcall_operator_workbench(
            db,
            current_user,
            view=view,
            voice_status=voice_status,
            ticket_id=ticket_id,
            limit=limit,
        )
