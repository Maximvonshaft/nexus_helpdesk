from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_control_plane import (
    PersonaProfileCreate,
    PersonaProfileDetailOut,
    PersonaProfileListOut,
    PersonaProfileOut,
    PersonaProfileUpdate,
    PersonaProfileVersionOut,
    PersonaPublishRequest,
    PersonaResolvePreviewOut,
    PersonaResolvePreviewRequest,
    PersonaRollbackRequest,
)
from ..services.permissions import ensure_can_manage_ai_configs, ensure_can_read_ai_configs
from ..services import persona_service
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/persona-profiles", tags=["persona-profiles"])


def _profile_out(row) -> PersonaProfileOut:
    return PersonaProfileOut.model_validate(row)


def _detail_out(db: Session, row) -> PersonaProfileDetailOut:
    versions = [PersonaProfileVersionOut.model_validate(item) for item in persona_service.list_versions(db, row.id)]
    return PersonaProfileDetailOut.model_validate(row).model_copy(update={"versions": versions})


@router.get("", response_model=PersonaProfileListOut)
def list_persona_profiles(
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    rows, total = persona_service.list_profiles(
        db,
        market_id=market_id,
        channel=channel,
        language=language,
        is_active=is_active,
        q=q,
        limit=limit,
        offset=offset,
    )
    return PersonaProfileListOut(profiles=[_profile_out(row) for row in rows], total=total)


@router.post("", response_model=PersonaProfileOut)
def create_persona_profile(
    payload: PersonaProfileCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = persona_service.create_profile(db, payload, current_user)
    db.refresh(row)
    return _profile_out(row)


@router.post("/resolve-preview", response_model=PersonaResolvePreviewOut)
def resolve_persona_preview(
    payload: PersonaResolvePreviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    row, score = persona_service.resolve_preview(
        db,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
    )
    return PersonaResolvePreviewOut(profile=_profile_out(row) if row else None, match_rank=score)


@router.get("/{profile_id}", response_model=PersonaProfileDetailOut)
def get_persona_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    return _detail_out(db, row)


@router.patch("/{profile_id}", response_model=PersonaProfileOut)
def update_persona_profile(
    profile_id: int,
    payload: PersonaProfileUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        row = persona_service.update_profile(db, row, payload, current_user)
    db.refresh(row)
    return _profile_out(row)


@router.post("/{profile_id}/publish", response_model=PersonaProfileVersionOut)
def publish_persona_profile(
    profile_id: int,
    payload: PersonaPublishRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        version_row = persona_service.publish_profile(db, row, current_user, notes=payload.notes)
    db.refresh(version_row)
    return PersonaProfileVersionOut.model_validate(version_row)


@router.post("/{profile_id}/rollback", response_model=PersonaProfileVersionOut)
def rollback_persona_profile(
    profile_id: int,
    payload: PersonaRollbackRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        version_row = persona_service.rollback_profile(db, row, version=payload.version, actor=current_user, notes=payload.notes)
    db.refresh(version_row)
    return PersonaProfileVersionOut.model_validate(version_row)
