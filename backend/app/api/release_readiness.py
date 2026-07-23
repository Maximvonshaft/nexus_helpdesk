from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_can_manage_runtime
from ..services.release_readiness import evaluate_release_readiness
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/release-readiness", tags=["release-readiness"])


@router.get("")
def release_readiness(
    profile: str = Query(default="controlled", pattern="^(controlled|provider_canary|full)$"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        return evaluate_release_readiness(db, profile=profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="release_profile_invalid") from exc
