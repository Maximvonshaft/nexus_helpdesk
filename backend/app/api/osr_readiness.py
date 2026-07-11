from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..services.ai_reply_contract import runtime_contract_secret_ready
from ..services.nexus_osr.business_readiness_service import collect_business_readiness
from ..services.release_metadata import runtime_identity_status
from ..services.storage_readiness import check_storage_readiness
from ..services.permissions import ensure_can_manage_runtime
from ..settings import get_settings
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/osr", tags=["admin-osr-readiness"])


def _ensure_osr_admin(current_user: Any, db: Session) -> None:
    if getattr(current_user, "role", None) != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="osr_admin_required")
    ensure_can_manage_runtime(current_user, db)


def _observed_migration_head(db: Session) -> str | None:
    try:
        row = db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
    except Exception:
        return None
    return str(row[0]) if row and row[0] else None


@router.get("/business-readiness")
def business_readiness(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Return bounded read-only OSR business readiness evidence.

    This endpoint does not call external Providers, execute tools, enqueue work,
    mutate policy or change readiness state. Missing evidence fails closed.
    """

    _ensure_osr_admin(current_user, db)
    settings = get_settings()
    storage = check_storage_readiness()
    signing = runtime_contract_secret_ready()
    observed_head = _observed_migration_head(db)
    evaluation = collect_business_readiness(
        db,
        settings=settings,
        observed_migration_head=observed_head,
        expected_migration_head=getattr(settings, "expected_migration_head", None),
        storage_ready=bool(storage.ok),
        runtime_signing_ready=bool(signing.get("ok")),
        profile_name=getattr(settings, "nexus_osr_release_profile", None),
    )
    return {
        **evaluation.as_dict(),
        "database": "ok",
        "observed_migration_head": observed_head,
        "storage": storage.as_dict(),
        "runtime_contract_signing": signing,
        "release_identity": runtime_identity_status(default_app_version="server"),
        "read_only": True,
    }
