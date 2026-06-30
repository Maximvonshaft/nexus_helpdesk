from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
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


class StatusDictionaryEntryRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    label: str = Field("", max_length=240)
    desc: str = Field("", max_length=1000)
    action: str = Field("", max_length=1000)
    language_labels: dict[str, str] = Field(default_factory=dict)
    needs_human: bool = False
    promise_eta: bool = False


class StatusDictionaryWriteRequest(BaseModel):
    entry: StatusDictionaryEntryRequest | None = None
    entries: list[StatusDictionaryEntryRequest] | None = None


def _ensure_can_read_support_intelligence(user, db: Session) -> None:
    capabilities = resolve_capabilities(user, db)
    allowed = {
        CAP_AI_CONFIG_READ,
        CAP_AI_CONFIG_MANAGE,
        CAP_QA_MANAGE,
        CAP_RUNTIME_MANAGE,
        CAP_TICKET_READ,
    }
    if user.role == UserRole.admin or capabilities & allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="support_intelligence_requires_management_capability",
    )


def _ensure_can_manage_support_intelligence(user, db: Session) -> None:
    capabilities = resolve_capabilities(user, db)
    allowed = {
        CAP_AI_CONFIG_MANAGE,
        CAP_RUNTIME_MANAGE,
    }
    if user.role == UserRole.admin or capabilities & allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="support_intelligence_requires_config_management_capability",
    )


def _ensure_can_publish_support_intelligence(user, db: Session) -> None:
    capabilities = resolve_capabilities(user, db)
    if user.role == UserRole.admin or CAP_RUNTIME_MANAGE in capabilities:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="support_intelligence_requires_runtime_publish_capability",
    )


def _redact_operator_response(config: dict) -> dict:
    for source in config.get("runtime_sources", []):
        if isinstance(source, dict):
            source.pop("source_path", None)
    for card in config.get("runtime_knowledge_cards", []):
        if isinstance(card, dict):
            card.pop("source_path", None)
            card.pop("workspace_path", None)
            card.pop("path", None)
    return config


def _bridge_status_dictionary(payload: dict) -> dict:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="legacy_status_dictionary_runtime_bridge_retired",
    )


def _status_write_payload(request: StatusDictionaryWriteRequest, current_user) -> dict:
    entries = []
    if request.entry is not None:
        entries.append(request.entry.model_dump())
    if request.entries:
        entries.extend(entry.model_dump() for entry in request.entries)
    if not entries:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status_dictionary_entry_required")
    return {
        "entries": entries,
        "operator_id": getattr(current_user, "username", None) or str(getattr(current_user, "id", "")),
    }


@router.get("/config")
def get_support_intelligence_config(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_read_support_intelligence(current_user, db)
    return _redact_operator_response(build_support_intelligence_config(db))


@router.get("/status-dictionary")
def get_status_dictionary(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_read_support_intelligence(current_user, db)
    return _bridge_status_dictionary({"operation": "status-dictionary-list"})


@router.post("/status-dictionary/draft")
def save_status_dictionary_draft(
    request: StatusDictionaryWriteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_manage_support_intelligence(current_user, db)
    return _bridge_status_dictionary({
        "operation": "status-dictionary-save-draft",
        **_status_write_payload(request, current_user),
    })


@router.post("/status-dictionary/publish")
def publish_status_dictionary(
    request: StatusDictionaryWriteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_publish_support_intelligence(current_user, db)
    return _bridge_status_dictionary({
        "operation": "status-dictionary-publish",
        **_status_write_payload(request, current_user),
    })
