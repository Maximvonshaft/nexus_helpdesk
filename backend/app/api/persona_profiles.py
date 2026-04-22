from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.audit_service import log_admin_audit
from ..services.permissions import ensure_can_manage_ai_configs
from ..services.persona_service import build_preview_payload, create_profile, list_profiles, list_versions, publish_profile, resolve_effective_profile, rollback_profile, update_profile
from ..models_control_plane import PersonaProfile
from ..unit_of_work import managed_session
from .deps import get_current_user


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_dt(self, value: Any):
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class PersonaProfileRead(APIModel):
    id: int
    profile_key: str
    name: str
    description: str | None = None
    market_id: int | None = None
    channel: str | None = None
    language: str | None = None
    is_active: bool
    draft_summary: str | None = None
    draft_content_json: dict[str, Any] | None = None
    published_summary: str | None = None
    published_content_json: dict[str, Any] | None = None
    published_version: int
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PersonaProfileCreate(BaseModel):
    profile_key: str
    name: str
    description: str | None = None
    market_id: int | None = None
    channel: str | None = None
    language: str | None = None
    is_active: bool = True
    draft_summary: str | None = None
    draft_content_json: dict[str, Any] = Field(default_factory=dict)


class PersonaProfileUpdate(BaseModel):
    profile_key: str | None = None
    name: str | None = None
    description: str | None = None
    market_id: int | None = None
    channel: str | None = None
    language: str | None = None
    is_active: bool | None = None
    draft_summary: str | None = None
    draft_content_json: dict[str, Any] | None = None


class PublishRequest(BaseModel):
    notes: str | None = None


class PersonaVersionRead(APIModel):
    id: int
    profile_id: int
    version: int
    snapshot_json: dict[str, Any]
    summary: str | None = None
    notes: str | None = None
    published_by: int | None = None
    published_at: datetime


class PersonaPreviewRequest(BaseModel):
    profile_id: int | None = None
    use_draft: bool = False
    market_id: int | None = None
    channel: str | None = None
    language: str | None = None
    user_message: str = ""


class PersonaPreviewRead(BaseModel):
    matched_profile_id: int | None = None
    matched_profile_key: str | None = None
    preview_json: dict[str, Any]
    debug_steps: list[str] = Field(default_factory=list)


router = APIRouter(prefix="/api/admin/persona-profiles", tags=["admin-persona"])


@router.get("", response_model=list[PersonaProfileRead])
def list_persona_profiles(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    return [PersonaProfileRead.model_validate(row) for row in list_profiles(db)]


@router.post("", response_model=PersonaProfileRead)
def create_persona_profile(payload: PersonaProfileCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = create_profile(db, payload, getattr(current_user, "id", None))
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="persona_profile.create",
            target_type="persona_profile",
            target_id=row.id,
            old_value=None,
            new_value={"profile_key": row.profile_key, "market_id": row.market_id, "channel": row.channel, "language": row.language},
        )
    db.refresh(row)
    return PersonaProfileRead.model_validate(row)


@router.patch("/{profile_id}", response_model=PersonaProfileRead)
def update_persona_profile(profile_id: int, payload: PersonaProfileUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(PersonaProfile).filter(PersonaProfile.id == profile_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Persona profile not found")
    before = {
        "profile_key": row.profile_key,
        "market_id": row.market_id,
        "channel": row.channel,
        "language": row.language,
        "is_active": row.is_active,
        "draft_summary": row.draft_summary,
    }
    with managed_session(db):
        row = update_profile(db, row, payload, getattr(current_user, "id", None))
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="persona_profile.update",
            target_type="persona_profile",
            target_id=row.id,
            old_value=before,
            new_value={
                "profile_key": row.profile_key,
                "market_id": row.market_id,
                "channel": row.channel,
                "language": row.language,
                "is_active": row.is_active,
                "draft_summary": row.draft_summary,
            },
        )
    db.refresh(row)
    return PersonaProfileRead.model_validate(row)


@router.post("/{profile_id}/publish", response_model=PersonaVersionRead)
def publish_persona_profile(profile_id: int, payload: PublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(PersonaProfile).filter(PersonaProfile.id == profile_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Persona profile not found")
    with managed_session(db):
        version = publish_profile(db, row, getattr(current_user, "id", None), notes=payload.notes)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="persona_profile.publish",
            target_type="persona_profile",
            target_id=row.id,
            old_value={"published_version": row.published_version - 1},
            new_value={"published_version": row.published_version},
        )
    db.refresh(version)
    return PersonaVersionRead.model_validate(version)


@router.get("/{profile_id}/versions", response_model=list[PersonaVersionRead])
def get_persona_versions(profile_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    return [PersonaVersionRead.model_validate(row) for row in list_versions(db, profile_id)]


@router.post("/{profile_id}/rollback/{version_num}", response_model=PersonaVersionRead)
def rollback_persona_profile(profile_id: int, version_num: int, payload: PublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(PersonaProfile).filter(PersonaProfile.id == profile_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Persona profile not found")
    with managed_session(db):
        version = rollback_profile(db, row, version_num, getattr(current_user, "id", None), notes=payload.notes)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="persona_profile.rollback",
            target_type="persona_profile",
            target_id=row.id,
            old_value={"requested_version": version_num - 1},
            new_value={"requested_version": version_num, "published_version": row.published_version},
        )
    db.refresh(version)
    return PersonaVersionRead.model_validate(version)


@router.post("/resolve-preview", response_model=PersonaPreviewRead)
def preview_persona_resolution(payload: PersonaPreviewRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    debug_steps: list[str] = []
    row = None
    if payload.profile_id is not None:
        row = db.query(PersonaProfile).filter(PersonaProfile.id == payload.profile_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Persona profile not found")
        debug_steps.append("mode=explicit_profile")
    else:
        row, reasons = resolve_effective_profile(
            db,
            market_id=payload.market_id,
            channel=payload.channel,
            language=payload.language,
        )
        debug_steps.extend(reasons)
    preview = build_preview_payload(
        row,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        user_message=payload.user_message,
        use_draft=payload.use_draft,
    )
    return PersonaPreviewRead(
        matched_profile_id=row.id if row else None,
        matched_profile_key=row.profile_key if row else None,
        preview_json=preview,
        debug_steps=debug_steps,
    )
