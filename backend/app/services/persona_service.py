from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models_control_plane import PersonaProfile, PersonaProfileVersion
from ..utils.time import utc_now


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _has_draft_content(row: PersonaProfile) -> bool:
    summary = (row.draft_summary or "").strip()
    content = row.draft_content_json or {}
    return bool(summary) or bool(content)


def _snapshot(row: PersonaProfile, *, version: int, published_at) -> dict:
    return {
        "profile_key": row.profile_key,
        "name": row.name,
        "description": row.description,
        "summary": row.draft_summary,
        "content_json": row.draft_content_json or {},
        "market_id": row.market_id,
        "channel": row.channel,
        "language": row.language,
        "published_version": version,
        "published_at": published_at.isoformat() if published_at else None,
    }


def list_profiles(
    db: Session,
    *,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    language: Optional[str] = None,
    is_active: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PersonaProfile], int]:
    query = db.query(PersonaProfile)
    if market_id is not None:
        query = query.filter(PersonaProfile.market_id == market_id)
    if channel:
        query = query.filter(PersonaProfile.channel == channel.strip())
    if language:
        query = query.filter(PersonaProfile.language == language.strip())
    if is_active is not None:
        query = query.filter(PersonaProfile.is_active.is_(is_active))
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(or_(PersonaProfile.profile_key.ilike(needle), PersonaProfile.name.ilike(needle)))
    total = query.count()
    rows = query.order_by(PersonaProfile.profile_key.asc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return rows, total


def get_profile_or_404(db: Session, profile_id: int) -> PersonaProfile:
    row = db.query(PersonaProfile).filter(PersonaProfile.id == profile_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Persona profile not found")
    return row


def list_versions(db: Session, profile_id: int) -> list[PersonaProfileVersion]:
    return db.query(PersonaProfileVersion).filter(PersonaProfileVersion.profile_id == profile_id).order_by(PersonaProfileVersion.version.desc()).all()


def create_profile(db: Session, payload, actor) -> PersonaProfile:
    key = _normalize_key(payload.profile_key)
    if db.query(PersonaProfile).filter(PersonaProfile.profile_key == key).first() is not None:
        raise HTTPException(status_code=409, detail="profile_key already exists")
    row = PersonaProfile(
        profile_key=key,
        name=payload.name,
        description=payload.description,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
        is_active=payload.is_active,
        draft_summary=payload.draft_summary,
        draft_content_json=payload.draft_content_json or {},
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    return row


def update_profile(db: Session, row: PersonaProfile, payload, actor) -> PersonaProfile:
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def publish_profile(db: Session, row: PersonaProfile, actor, *, notes: Optional[str] = None) -> PersonaProfileVersion:
    if not _has_draft_content(row):
        raise HTTPException(status_code=400, detail="Draft persona content is empty")
    new_version = (row.published_version or 0) + 1
    published_at = utc_now()
    version_row = PersonaProfileVersion(
        profile_id=row.id,
        version=new_version,
        snapshot_json=_snapshot(row, version=new_version, published_at=published_at),
        summary=row.draft_summary,
        notes=notes,
        published_by=getattr(actor, "id", None),
        published_at=published_at,
    )
    row.published_summary = row.draft_summary
    row.published_content_json = row.draft_content_json or {}
    row.published_version = new_version
    row.published_at = published_at
    row.published_by = getattr(actor, "id", None)
    row.updated_by = getattr(actor, "id", None)
    db.add(version_row)
    db.flush()
    return version_row


def rollback_profile(db: Session, row: PersonaProfile, *, version: int, actor, notes: Optional[str] = None) -> PersonaProfileVersion:
    target = db.query(PersonaProfileVersion).filter(
        PersonaProfileVersion.profile_id == row.id,
        PersonaProfileVersion.version == version,
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Persona profile version not found")
    snapshot = target.snapshot_json or {}
    row.draft_summary = snapshot.get("summary")
    row.draft_content_json = snapshot.get("content_json") or {}
    row.name = snapshot.get("name") or row.name
    row.description = snapshot.get("description")
    row.market_id = snapshot.get("market_id")
    row.channel = snapshot.get("channel")
    row.language = snapshot.get("language")
    return publish_profile(db, row, actor, notes=notes or f"Rollback to v{version}")


def resolve_preview(
    db: Session,
    *,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    language: Optional[str] = None,
) -> tuple[Optional[PersonaProfile], Optional[int]]:
    rows = db.query(PersonaProfile).filter(
        PersonaProfile.is_active.is_(True),
        PersonaProfile.published_version > 0,
    ).all()

    def rank(row: PersonaProfile) -> Optional[int]:
        market_match = market_id is not None and row.market_id == market_id
        market_global = row.market_id is None
        channel_match = bool(channel) and row.channel == channel
        channel_global = row.channel is None
        language_match = bool(language) and row.language == language
        language_global = row.language is None

        if market_match and channel_match and language_match:
            return 1
        if market_match and channel_match and language_global:
            return 2
        if market_match and channel_global and language_match:
            return 3
        if market_match and channel_global and language_global:
            return 4
        if market_global and channel_match and language_match:
            return 5
        if market_global and channel_match and language_global:
            return 6
        if market_global and channel_global and language_global:
            return 7
        return None

    ranked = [(rank(row), row) for row in rows]
    ranked = [(score, row) for score, row in ranked if score is not None]
    if not ranked:
        return None, None
    ranked.sort(key=lambda item: (item[0], item[1].profile_key))
    score, row = ranked[0]
    return row, score
