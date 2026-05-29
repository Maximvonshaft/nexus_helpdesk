from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import TodayWorkbenchRead
from ..services.today_workbench_service import build_today_workbench
from .deps import get_current_user

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


@router.get("/today", response_model=TodayWorkbenchRead)
def get_today_workbench(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return build_today_workbench(db, current_user)
