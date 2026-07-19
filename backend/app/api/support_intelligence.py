from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_AI_CONFIG_READ,
    CAP_QA_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_TICKET_READ,
    resolve_capabilities,
)
from ..services.support_intelligence_service import build_support_intelligence_config
from .deps import get_current_user

router = APIRouter(prefix="/api/support-intelligence", tags=["support-intelligence"])


def _ensure_capability(user, db: Session, *, allowed: set[str], detail: str) -> None:
    if resolve_capabilities(user, db) & allowed:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _ensure_can_read_support_intelligence(user, db: Session) -> None:
    _ensure_capability(
        user,
        db,
        allowed={
            CAP_AI_CONFIG_READ,
            CAP_AI_CONFIG_MANAGE,
            CAP_QA_MANAGE,
            CAP_RUNTIME_MANAGE,
            CAP_TICKET_READ,
        },
        detail="support_intelligence_requires_management_capability",
    )


def _ensure_can_manage_support_intelligence(user, db: Session) -> None:
    _ensure_capability(
        user,
        db,
        allowed={CAP_AI_CONFIG_MANAGE, CAP_RUNTIME_MANAGE},
        detail="support_intelligence_requires_config_management_capability",
    )


def _ensure_can_publish_support_intelligence(user, db: Session) -> None:
    _ensure_capability(
        user,
        db,
        allowed={CAP_RUNTIME_MANAGE},
        detail="support_intelligence_requires_runtime_publish_capability",
    )


def _redact_operator_response(config: dict) -> dict:
    for source in config.get("runtime_sources", []):
        if isinstance(source, dict):
            source.pop("source_path", None)
    return config


@router.get("/config")
def get_support_intelligence_config(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_read_support_intelligence(current_user, db)
    return _redact_operator_response(build_support_intelligence_config(db))
