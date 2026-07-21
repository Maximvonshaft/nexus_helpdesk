from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models_control_plane import PersonaProfile, PersonaProfileReview, PersonaProfileVersion
from ..utils.time import ensure_utc, utc_now
from .agent_resource_authority import (
    PERSONA_RESOURCE,
    actor_tenant_key,
    bind_resource,
    ensure_resource_manageable,
    ensure_resource_visible,
    manageable_resource_ids,
    session_actor,
    visible_resource_ids,
)
from .persona_contract import validate_persona_content


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _tenant_key(tenant_key: str, value: str) -> str:
    key = _normalize_key(value)
    if tenant_key == "default":
        return key[:120]
    prefix = f"{tenant_key}."
    return (key if key.startswith(prefix) else f"{prefix}{key}")[:120]


def _has_draft_content(row: PersonaProfile) -> bool:
    return bool((row.draft_summary or "").strip()) or bool(row.draft_content_json or {})


def _validated_content(row: PersonaProfile) -> dict:
    return validate_persona_content(row.draft_content_json or {})


def _snapshot(row: PersonaProfile, *, version: int, published_at) -> dict:
    return {
        "profile_key": row.profile_key,
        "name": row.name,
        "description": row.description,
        "summary": row.draft_summary,
        "content_json": _validated_content(row),
        "market_id": row.market_id,
        "channel": row.channel,
        "language": row.language,
        "published_version": version,
        "published_at": published_at.isoformat() if published_at else None,
    }


def _review_snapshot(row: PersonaProfile) -> dict:
    snapshot = _snapshot(row, version=row.published_version or 0, published_at=row.published_at)
    snapshot["draft_updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    snapshot["published_version_at_submission"] = row.published_version or 0
    return snapshot


def _validate_release_window(start, end) -> None:
    start = ensure_utc(start)
    end = ensure_utc(end)
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="release_window_start must be before release_window_end")


def _next_review_version(db: Session, profile_id: int) -> int:
    latest = (
        db.query(PersonaProfileReview.review_version)
        .filter(PersonaProfileReview.profile_id == profile_id)
        .order_by(PersonaProfileReview.review_version.desc())
        .first()
    )
    return int(latest[0] if latest else 0) + 1


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
    actor = session_actor(db)
    if actor is not None:
        allowed = visible_resource_ids(
            db,
            resource_type=PERSONA_RESOURCE,
            actor=actor,
            include_global_templates=True,
        )
        if allowed is not None:
            if not allowed:
                return [], 0
            query = query.filter(PersonaProfile.id.in_(allowed))
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
        query = query.filter(
            or_(PersonaProfile.profile_key.ilike(needle), PersonaProfile.name.ilike(needle))
        )
    total = query.count()
    rows = (
        query.order_by(PersonaProfile.profile_key.asc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return rows, total


def get_profile_or_404(db: Session, profile_id: int) -> PersonaProfile:
    row = db.query(PersonaProfile).filter(PersonaProfile.id == profile_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Persona profile not found")
    if session_actor(db) is not None:
        ensure_resource_visible(db, resource_type=PERSONA_RESOURCE, resource_id=row.id)
    return row


def list_versions(db: Session, profile_id: int) -> list[PersonaProfileVersion]:
    get_profile_or_404(db, profile_id)
    return (
        db.query(PersonaProfileVersion)
        .filter(PersonaProfileVersion.profile_id == profile_id)
        .order_by(PersonaProfileVersion.version.desc())
        .all()
    )


def list_reviews(
    db: Session,
    *,
    profile_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PersonaProfileReview], int]:
    query = db.query(PersonaProfileReview)
    actor = session_actor(db)
    if actor is not None:
        allowed = visible_resource_ids(
            db,
            resource_type=PERSONA_RESOURCE,
            actor=actor,
            include_global_templates=True,
        )
        if allowed is not None:
            if not allowed:
                return [], 0
            query = query.filter(PersonaProfileReview.profile_id.in_(allowed))
    if profile_id is not None:
        query = query.filter(PersonaProfileReview.profile_id == profile_id)
    if status:
        query = query.filter(PersonaProfileReview.status == status.strip())
    total = query.count()
    rows = (
        query.order_by(PersonaProfileReview.requested_at.desc(), PersonaProfileReview.id.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return rows, total


def get_review_or_404(db: Session, review_id: int) -> PersonaProfileReview:
    row = db.query(PersonaProfileReview).filter(PersonaProfileReview.id == review_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Persona profile review not found")
    get_profile_or_404(db, row.profile_id)
    return row


def create_profile(db: Session, payload, actor) -> PersonaProfile:
    tenant_key = actor_tenant_key(db, actor)
    key = _tenant_key(tenant_key, payload.profile_key)
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
        draft_content_json=validate_persona_content(payload.draft_content_json or {}),
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    bind_resource(
        db,
        resource_type=PERSONA_RESOURCE,
        resource_id=row.id,
        tenant_key=tenant_key,
        actor_id=getattr(actor, "id", None),
        is_global_template=getattr(actor, "tenant_id", None) is None and tenant_key == "default",
    )
    return row


def update_profile(db: Session, row: PersonaProfile, payload, actor) -> PersonaProfile:
    ensure_resource_manageable(
        db,
        resource_type=PERSONA_RESOURCE,
        resource_id=row.id,
        actor=actor,
    )
    values = payload.model_dump(exclude_unset=True)
    if "draft_content_json" in values:
        values["draft_content_json"] = validate_persona_content(values["draft_content_json"] or {})
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def publish_profile(
    db: Session,
    row: PersonaProfile,
    actor,
    *,
    notes: Optional[str] = None,
) -> PersonaProfileVersion:
    ensure_resource_manageable(
        db,
        resource_type=PERSONA_RESOURCE,
        resource_id=row.id,
        actor=actor,
    )
    if not _has_draft_content(row):
        raise HTTPException(status_code=400, detail="Draft persona content is empty")
    _ensure_no_scope_conflict(db, row)
    content = _validated_content(row)
    new_version = (row.published_version or 0) + 1
    published_at = utc_now()
    row.draft_content_json = content
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
    row.published_content_json = content
    row.published_version = new_version
    row.published_at = published_at
    row.published_by = getattr(actor, "id", None)
    row.updated_by = getattr(actor, "id", None)
    db.add(version_row)
    db.flush()
    return version_row


def rollback_profile(
    db: Session,
    row: PersonaProfile,
    *,
    version: int,
    actor,
    notes: Optional[str] = None,
) -> PersonaProfileVersion:
    ensure_resource_manageable(
        db,
        resource_type=PERSONA_RESOURCE,
        resource_id=row.id,
        actor=actor,
    )
    target = (
        db.query(PersonaProfileVersion)
        .filter(
            PersonaProfileVersion.profile_id == row.id,
            PersonaProfileVersion.version == version,
        )
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Persona profile version not found")
    snapshot = target.snapshot_json or {}
    row.draft_summary = snapshot.get("summary")
    row.draft_content_json = validate_persona_content(snapshot.get("content_json") or {})
    row.name = snapshot.get("name") or row.name
    row.description = snapshot.get("description")
    row.market_id = snapshot.get("market_id")
    row.channel = snapshot.get("channel")
    row.language = snapshot.get("language")
    return publish_profile(db, row, actor, notes=notes or f"Rollback to v{version}")


def submit_review(db: Session, row: PersonaProfile, payload, actor) -> PersonaProfileReview:
    ensure_resource_manageable(db, resource_type=PERSONA_RESOURCE, resource_id=row.id, actor=actor)
    if not _has_draft_content(row):
        raise HTTPException(status_code=400, detail="Draft persona content is empty")
    _validated_content(row)
    existing = (
        db.query(PersonaProfileReview)
        .filter(
            PersonaProfileReview.profile_id == row.id,
            PersonaProfileReview.status == "pending",
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="persona_review_already_pending")
    _validate_release_window(payload.release_window_start, payload.release_window_end)
    now = utc_now()
    review = PersonaProfileReview(
        profile_id=row.id,
        review_version=_next_review_version(db, row.id),
        status="pending",
        snapshot_json=_review_snapshot(row),
        summary=row.draft_summary,
        notes=payload.notes,
        requested_by=getattr(actor, "id", None),
        requested_at=now,
        release_window_start=payload.release_window_start,
        release_window_end=payload.release_window_end,
        created_at=now,
        updated_at=now,
    )
    db.add(review)
    db.flush()
    return review


def approve_review(db: Session, review: PersonaProfileReview, payload, actor) -> PersonaProfileReview:
    _ensure_review_manageable(db, review, actor)
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="persona_review_not_pending")
    if review.requested_by == getattr(actor, "id", None):
        raise HTTPException(status_code=409, detail="persona_review_requires_independent_approver")
    start = payload.release_window_start if payload.release_window_start is not None else review.release_window_start
    end = payload.release_window_end if payload.release_window_end is not None else review.release_window_end
    _validate_release_window(start, end)
    review.status = "approved"
    review.reviewed_by = getattr(actor, "id", None)
    review.reviewed_at = utc_now()
    review.decision_note = payload.decision_note
    review.release_window_start = start
    review.release_window_end = end
    review.updated_at = utc_now()
    db.flush()
    return review


def reject_review(db: Session, review: PersonaProfileReview, payload, actor) -> PersonaProfileReview:
    _ensure_review_manageable(db, review, actor)
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="persona_review_not_pending")
    if review.requested_by == getattr(actor, "id", None):
        raise HTTPException(status_code=409, detail="persona_review_requires_independent_reviewer")
    review.status = "rejected"
    review.reviewed_by = getattr(actor, "id", None)
    review.reviewed_at = utc_now()
    review.decision_note = payload.decision_note
    review.updated_at = utc_now()
    db.flush()
    return review


def publish_approved_review(
    db: Session,
    review: PersonaProfileReview,
    actor,
    *,
    notes: Optional[str] = None,
) -> PersonaProfileVersion:
    _ensure_review_manageable(db, review, actor)
    if review.status != "approved":
        raise HTTPException(status_code=409, detail="persona_review_not_approved")
    now = utc_now()
    start = ensure_utc(review.release_window_start)
    end = ensure_utc(review.release_window_end)
    if start is not None and now < start:
        raise HTTPException(status_code=409, detail="persona_release_window_not_open")
    if end is not None and now > end:
        raise HTTPException(status_code=409, detail="persona_release_window_expired")
    profile = get_profile_or_404(db, review.profile_id)
    snapshot = review.snapshot_json or {}
    profile.name = snapshot.get("name") or profile.name
    profile.description = snapshot.get("description")
    profile.market_id = snapshot.get("market_id")
    profile.channel = snapshot.get("channel")
    profile.language = snapshot.get("language")
    profile.draft_summary = snapshot.get("summary")
    profile.draft_content_json = validate_persona_content(snapshot.get("content_json") or {})
    version_row = publish_profile(db, profile, actor, notes=notes or f"Publish approved review #{review.id}")
    review.status = "published"
    review.published_by = getattr(actor, "id", None)
    review.published_version = version_row.version
    review.published_at = version_row.published_at
    review.updated_at = now
    db.flush()
    return version_row


def resolve_preview(
    db: Session,
    *,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    language: Optional[str] = None,
) -> tuple[Optional[PersonaProfile], Optional[int]]:
    rows, _ = list_profiles(
        db,
        market_id=None,
        channel=None,
        language=None,
        is_active=True,
        q=None,
        limit=200,
        offset=0,
    )
    rows = [row for row in rows if int(row.published_version or 0) > 0]

    def rank(row: PersonaProfile) -> Optional[int]:
        values = (
            (row.market_id, market_id, 4),
            (row.channel, channel, 2),
            (row.language, language, 1),
        )
        score = 0
        for expected, actual, weight in values:
            if expected is None:
                continue
            if expected != actual:
                return None
            score += weight
        return 7 - score

    ranked = [(score, row) for row in rows if (score := rank(row)) is not None]
    if not ranked:
        return None, None
    best = min(score for score, _ in ranked)
    matches = [row for score, row in ranked if score == best]
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="ambiguous_persona_scope")
    return matches[0], best


def _ensure_review_manageable(db: Session, review: PersonaProfileReview, actor) -> None:
    ensure_resource_manageable(
        db,
        resource_type=PERSONA_RESOURCE,
        resource_id=review.profile_id,
        actor=actor,
    )


def _ensure_no_scope_conflict(db: Session, row: PersonaProfile) -> None:
    allowed = manageable_resource_ids(db, resource_type=PERSONA_RESOURCE)
    query = db.query(PersonaProfile).filter(
        PersonaProfile.id != row.id,
        PersonaProfile.is_active.is_(True),
        PersonaProfile.published_version > 0,
        PersonaProfile.market_id.is_(None) if row.market_id is None else PersonaProfile.market_id == row.market_id,
        PersonaProfile.channel.is_(None) if row.channel is None else PersonaProfile.channel == row.channel,
        PersonaProfile.language.is_(None) if row.language is None else PersonaProfile.language == row.language,
    )
    if allowed is not None:
        if not allowed:
            return
        query = query.filter(PersonaProfile.id.in_(allowed))
    if query.first() is not None:
        raise HTTPException(status_code=409, detail="persona_scope_conflict")
