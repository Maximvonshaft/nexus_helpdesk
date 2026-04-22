from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Market
from ..models_control_plane import PersonaProfile, PersonaProfileVersion

VALID_PERSONA_CHANNELS = {"whatsapp", "telegram", "sms", "email", "web_chat", "internal"}


def normalize_key(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)


def normalize_language(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned or None


def normalize_channel(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    if cleaned and cleaned not in VALID_PERSONA_CHANNELS:
        raise HTTPException(status_code=400, detail="Unsupported persona channel")
    return cleaned or None


def _ensure_market_exists(db: Session, market_id: int | None) -> None:
    if market_id is None:
        return
    if db.query(Market.id).filter(Market.id == market_id, Market.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=400, detail="Market not found or inactive")


def list_profiles(db: Session) -> list[PersonaProfile]:
    return db.query(PersonaProfile).order_by(PersonaProfile.name.asc(), PersonaProfile.id.asc()).all()


def create_profile(db: Session, payload: Any, actor_id: int | None) -> PersonaProfile:
    key = normalize_key(payload.profile_key)
    if db.query(PersonaProfile.id).filter(PersonaProfile.profile_key == key).first():
        raise HTTPException(status_code=409, detail="profile_key already exists")
    _ensure_market_exists(db, payload.market_id)
    row = PersonaProfile(
        profile_key=key,
        name=payload.name.strip(),
        description=(payload.description or "").strip() or None,
        market_id=payload.market_id,
        channel=normalize_channel(payload.channel),
        language=normalize_language(payload.language),
        is_active=payload.is_active,
        draft_summary=(payload.draft_summary or "").strip() or None,
        draft_content_json=payload.draft_content_json or {},
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def update_profile(db: Session, row: PersonaProfile, payload: Any, actor_id: int | None) -> PersonaProfile:
    values = payload.model_dump(exclude_unset=True)
    if "profile_key" in values and values["profile_key"] is not None:
        values["profile_key"] = normalize_key(values["profile_key"])
        existing = db.query(PersonaProfile.id).filter(PersonaProfile.profile_key == values["profile_key"], PersonaProfile.id != row.id).first()
        if existing:
            raise HTTPException(status_code=409, detail="profile_key already exists")
    if "market_id" in values:
        _ensure_market_exists(db, values["market_id"])
    if "channel" in values:
        values["channel"] = normalize_channel(values["channel"])
    if "language" in values:
        values["language"] = normalize_language(values["language"])
    if "name" in values and values["name"] is not None:
        values["name"] = values["name"].strip()
    if "description" in values and values["description"] is not None:
        values["description"] = values["description"].strip() or None
    if "draft_summary" in values and values["draft_summary"] is not None:
        values["draft_summary"] = values["draft_summary"].strip() or None
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = actor_id
    db.flush()
    return row


def publish_profile(db: Session, row: PersonaProfile, actor_id: int | None, *, notes: str | None = None) -> PersonaProfileVersion:
    snapshot = row.draft_content_json or {}
    if not isinstance(snapshot, dict) or not snapshot:
        raise HTTPException(status_code=400, detail="Draft persona content is empty")
    version_num = (row.published_version or 0) + 1
    version = PersonaProfileVersion(
        profile_id=row.id,
        version=version_num,
        snapshot_json=snapshot,
        summary=row.draft_summary,
        notes=(notes or "").strip() or None,
        published_by=actor_id,
    )
    row.published_content_json = snapshot
    row.published_summary = row.draft_summary
    row.published_version = version_num
    row.published_by = actor_id
    row.published_at = version.published_at
    row.updated_by = actor_id
    db.add(version)
    db.flush()
    return version


def list_versions(db: Session, profile_id: int) -> list[PersonaProfileVersion]:
    return db.query(PersonaProfileVersion).filter(PersonaProfileVersion.profile_id == profile_id).order_by(PersonaProfileVersion.version.desc()).all()


def rollback_profile(db: Session, row: PersonaProfile, version_num: int, actor_id: int | None, *, notes: str | None = None) -> PersonaProfileVersion:
    target = db.query(PersonaProfileVersion).filter(PersonaProfileVersion.profile_id == row.id, PersonaProfileVersion.version == version_num).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Persona version not found")
    row.draft_content_json = target.snapshot_json
    row.draft_summary = target.summary
    return publish_profile(db, row, actor_id, notes=notes or f"Rollback to v{version_num}")


def resolve_effective_profile(
    db: Session,
    *,
    market_id: int | None,
    channel: str | None,
    language: str | None,
) -> tuple[PersonaProfile | None, list[str]]:
    normalized_channel = normalize_channel(channel)
    normalized_language = normalize_language(language)
    candidates = db.query(PersonaProfile).filter(
        PersonaProfile.is_active.is_(True),
        PersonaProfile.published_version > 0,
    ).all()
    scored: list[tuple[int, PersonaProfile, list[str]]] = []
    for row in candidates:
        reasons: list[str] = []
        score = 0
        if row.market_id is None:
            reasons.append("market=global")
        elif market_id is not None and row.market_id == market_id:
            reasons.append("market=exact")
            score += 100
        else:
            continue

        if row.channel is None:
            reasons.append("channel=global")
        elif normalized_channel and row.channel == normalized_channel:
            reasons.append("channel=exact")
            score += 20
        else:
            continue

        if row.language is None:
            reasons.append("language=global")
        elif normalized_language and row.language == normalized_language:
            reasons.append("language=exact")
            score += 10
        else:
            continue

        scored.append((score, row, reasons))

    if not scored:
        return None, ["no matching published persona profile"]
    scored.sort(key=lambda item: (-item[0], item[1].id))
    best = scored[0]
    return best[1], best[2]


def build_preview_payload(
    row: PersonaProfile | None,
    *,
    market_id: int | None,
    channel: str | None,
    language: str | None,
    user_message: str,
    use_draft: bool = False,
) -> dict[str, Any]:
    content = {}
    if row is not None:
        source = row.draft_content_json if use_draft else row.published_content_json
        if isinstance(source, dict):
            content = dict(source)
    return {
        "resolved_profile": {
            "id": row.id if row else None,
            "profile_key": row.profile_key if row else None,
            "name": row.name if row else None,
            "market_id": row.market_id if row else None,
            "channel": row.channel if row else None,
            "language": row.language if row else None,
            "version": row.published_version if row else 0,
            "mode": "draft" if use_draft else "published",
        },
        "context": {
            "market_id": market_id,
            "channel": normalize_channel(channel),
            "language": normalize_language(language),
            "user_message": user_message,
        },
        "persona": content,
    }
