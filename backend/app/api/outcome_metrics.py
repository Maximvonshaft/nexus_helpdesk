from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.business_outcome_metrics_access import (
    build_business_outcome_metrics_for_user,
)
from .deps import get_current_user

router = APIRouter(
    prefix="/api/lite/control-tower",
    tags=["lite", "control-tower", "outcome-metrics"],
)


@router.get("/outcomes")
def control_tower_outcome_metrics(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return build_business_outcome_metrics_for_user(db, current_user)
