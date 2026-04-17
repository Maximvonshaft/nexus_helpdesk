from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import AIConfigResource, AIConfigVersion


VALID_CONFIG_TYPES = {"persona", "knowledge", "sop", "policy"}
VALID_SCOPE_TYPES = {"global", "market", "team", "channel", "case_type"}


def normalize_resource_key(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)


def validate_config_shape(config_type: str, scope_type: str) -> None:
    if config_type not in VALID_CONFIG_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported config_type")
    if scope_type not in VALID_SCOPE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported scope_type")


def list_admin_resources(db: Session, *, config_type: Optional[str] = None):
    query = db.query(AIConfigResource)
    if config_type:
        query = query.filter(AIConfigResource.config_type == config_type)
    return query.order_by(AIConfigResource.config_type.asc(), AIConfigResource.name.asc()).all()


def list_published_resources(db: Session, *, config_type: Optional[str] = None, market_id: Optional[int] = None):
    query = db.query(AIConfigResource).filter(
        AIConfigResource.is_active.is_(True),
        AIConfigResource.published_version > 0,
    )
    if config_type:
        query = query.filter(AIConfigResource.config_type == config_type)
    if market_id is not None:
        query = query.filter(or_(AIConfigResource.market_id.is_(None), AIConfigResource.market_id == market_id))
    return query.order_by(AIConfigResource.config_type.asc(), AIConfigResource.name.asc()).all()


def create_resource(db: Session, payload, actor):
    key = normalize_resource_key(payload.resource_key)
    validate_config_shape(payload.config_type, payload.scope_type)
    if db.query(AIConfigResource).filter(AIConfigResource.resource_key == key).first():
        raise HTTPException(status_code=409, detail="resource_key already exists")
    row = AIConfigResource(
        resource_key=key,
        config_type=payload.config_type,
        name=payload.name,
        description=payload.description,
        scope_type=payload.scope_type,
        scope_value=payload.scope_value,
        market_id=payload.market_id,
        is_active=payload.is_active,
        draft_summary=payload.draft_summary,
        draft_content_json=payload.draft_content_json or {},
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    return row


def update_resource(db: Session, row: AIConfigResource, payload, actor):
    values = payload.model_dump(exclude_unset=True)
    if "resource_key" in values and values["resource_key"] is not None:
        values["resource_key"] = normalize_resource_key(values["resource_key"])
        existing = db.query(AIConfigResource).filter(
            AIConfigResource.resource_key == values["resource_key"],
            AIConfigResource.id != row.id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="resource_key already exists")
    validate_config_shape(values.get("config_type", row.config_type), values.get("scope_type", row.scope_type))
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def publish_resource(db: Session, row: AIConfigResource, actor, *, notes: Optional[str] = None):
    snapshot = row.draft_content_json or {}
    if not isinstance(snapshot, dict) or not snapshot:
        raise HTTPException(status_code=400, detail="Draft content is empty")
    new_version = (row.published_version or 0) + 1
    version_row = AIConfigVersion(
        resource_id=row.id,
        version=new_version,
        snapshot_json=snapshot,
        summary=row.draft_summary,
        notes=notes,
        published_by=getattr(actor, "id", None),
    )
    row.published_content_json = snapshot
    row.published_summary = row.draft_summary
    row.published_version = new_version
    row.published_by = getattr(actor, "id", None)
    row.published_at = version_row.published_at
    row.updated_by = getattr(actor, "id", None)
    db.add(version_row)
    db.flush()
    return version_row


def list_versions(db: Session, resource_id: int):
    return db.query(AIConfigVersion).filter(AIConfigVersion.resource_id == resource_id).order_by(AIConfigVersion.version.desc()).all()


def rollback_resource(db: Session, row: AIConfigResource, version: int, actor, *, notes: Optional[str] = None):
    target = db.query(AIConfigVersion).filter(AIConfigVersion.resource_id == row.id, AIConfigVersion.version == version).first()
    if not target:
        raise HTTPException(status_code=404, detail="AI config version not found")
    row.draft_content_json = target.snapshot_json
    row.draft_summary = target.summary
    rollback_notes = notes or f"Rollback to v{version}"
    return publish_resource(db, row, actor, notes=rollback_notes)
