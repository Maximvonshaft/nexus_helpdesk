from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.canonical_control_tower_service import CONTROL_TOWER_CAPABILITIES
from ..services.outcome_metrics_service import build_outcome_metrics
from ..services.permissions import resolve_capabilities
from .deps import get_current_user

router = APIRouter(
    prefix="/api/lite/control-tower",
    tags=["lite", "control-tower", "outcome-metrics"],
)


@router.get("/outcomes")
def control_tower_outcome_metrics(
    window_days: int = Query(default=30, ge=1, le=366),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not (resolve_capabilities(current_user, db) & CONTROL_TOWER_CAPABILITIES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="control_tower_requires_management_capability",
        )
    return build_outcome_metrics(
        db,
        current_user,
        window_days=window_days,
    )
