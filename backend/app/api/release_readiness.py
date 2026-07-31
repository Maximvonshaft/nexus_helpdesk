from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.activation_evidence_policy import finalize_release_readiness
from ..services.permissions import ensure_can_manage_runtime
from ..services.release_readiness import (
    evaluate_release_readiness as collect_release_readiness,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/release-readiness", tags=["release-readiness"])


def _configured_profile() -> str:
    value = str(os.getenv("PRODUCTION_PROFILE", "controlled") or "controlled").strip().lower()
    if value not in {"controlled", "provider_canary", "full"}:
        raise ValueError("release_profile_invalid")
    return value


@router.get("")
def release_readiness(
    profile: str | None = Query(
        default=None,
        pattern="^(controlled|provider_canary|full)$",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    try:
        profile = profile if profile is not None else _configured_profile()
        collected = collect_release_readiness(db, profile=profile)
        return finalize_release_readiness(collected)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="release_profile_invalid") from exc
