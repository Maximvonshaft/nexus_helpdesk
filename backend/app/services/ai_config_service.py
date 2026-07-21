from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import AIConfigResource, AIConfigVersion
from .agent_control_config import (
    CANONICAL_AGENT_CONFIG_TYPES,
    SINGLETON_TYPES,
    SUPPORTED_SCOPE_TYPES,
    validate_agent_config_content,
    validate_scope,
)
from .agent_resource_authority import (
    AI_CONFIG_RESOURCE,
    actor_tenant_key,
    bind_resource,
    ensure_resource_manageable,
    ensure_resource_visible,
    manageable_resource_ids,
    session_actor,
    visible_resource_ids,
)
from .agent_tool_contracts import bootstrap_agent_tool_contracts

bootstrap_agent_tool_contracts()

VALID_CONFIG_TYPES = set(CANONICAL_AGENT_CONFIG_TYPES)
VALID_SCOPE_TYPES = set(SUPPORTED_SCOPE_TYPES)


def normalize_resource_key(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)


def validate_config_shape(config_type: str, scope_type: str) -> None:
    if config_type not in VALID_CONFIG_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported config_type")
    if scope_type not in VALID_SCOPE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported scope_type")


def list_admin_resources(db: Session, *, config_type: Optional[str] = None):
    query = db.query(AIConfigResource).filter(AIConfigResource.config_type.in_(VALID_CONFIG_TYPES))
    allowed = manageable_resource_ids(db, resource_type=AI_CONFIG_RESOURCE)
    if allowed is not None:
        if not allowed:
            return []
        query = query.filter(AIConfigResource.id.in_(allowed))
    if config_type:
        if config_type not in VALID_CONFIG_TYPES:
            return []
        query = query.filter(AIConfigResource.config_type == config_type)
    return query.order_by(AIConfigResource.config_type.asc(), AIConfigResource.name.asc()).all()


def list_visible_resources(db: Session, *, config_type: Optional[str] = None):
    query = db.query(AIConfigResource).filter(AIConfigResource.config_type.in_(VALID_CONFIG_TYPES))
    allowed = visible_resource_ids(
        db,
        resource_type=AI_CONFIG_RESOURCE,
        include_global_templates=True,
    )
    if allowed is not None:
        if not allowed:
            return []
        query = query.filter(AIConfigResource.id.in_(allowed))
    if config_type:
        if config_type not in VALID_CONFIG_TYPES:
            return []
        query = query.filter(AIConfigResource.config_type == config_type)
    return query.order_by(AIConfigResource.config_type.asc(), AIConfigResource.name.asc()).all()


def list_published_resources(
    db: Session,
    *,
    config_type: Optional[str] = None,
    market_id: Optional[int] = None,
):
    query = db.query(AIConfigResource).filter(
        AIConfigResource.config_type.in_(VALID_CONFIG_TYPES),
        AIConfigResource.scope_type.in_(VALID_SCOPE_TYPES),
        AIConfigResource.is_active.is_(True),
        AIConfigResource.published_version > 0,
    )
    actor = session_actor(db)
    if actor is not None:
        allowed = visible_resource_ids(
            db,
            resource_type=AI_CONFIG_RESOURCE,
            actor=actor,
            include_global_templates=True,
        )
        if allowed is not None:
            if not allowed:
                return []
            query = query.filter(AIConfigResource.id.in_(allowed))
    if config_type:
        if config_type not in VALID_CONFIG_TYPES:
            return []
        query = query.filter(AIConfigResource.config_type == config_type)
    if market_id is not None:
        query = query.filter(
            or_(AIConfigResource.market_id.is_(None), AIConfigResource.market_id == market_id)
        )
    return query.order_by(AIConfigResource.config_type.asc(), AIConfigResource.name.asc()).all()


def create_resource(db: Session, payload, actor):
    tenant_key = actor_tenant_key(db, actor)
    key = _tenant_resource_key(tenant_key, payload.resource_key)
    validate_config_shape(payload.config_type, payload.scope_type)
    scope_type, scope_value = validate_scope(payload.scope_type, payload.scope_value)
    content = validate_agent_config_content(payload.config_type, payload.draft_content_json or {})
    if db.query(AIConfigResource).filter(AIConfigResource.resource_key == key).first():
        raise HTTPException(status_code=409, detail="resource_key already exists")
    _ensure_singleton_scope_available(
        db,
        config_type=payload.config_type,
        scope_type=scope_type,
        scope_value=scope_value,
        market_id=payload.market_id,
    )
    row = AIConfigResource(
        resource_key=key,
        config_type=payload.config_type,
        name=payload.name.strip(),
        description=payload.description,
        scope_type=scope_type,
        scope_value=scope_value,
        market_id=payload.market_id,
        is_active=payload.is_active,
        draft_summary=payload.draft_summary,
        draft_content_json=content,
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    bind_resource(
        db,
        resource_type=AI_CONFIG_RESOURCE,
        resource_id=row.id,
        tenant_key=tenant_key,
        actor_id=getattr(actor, "id", None),
        is_global_template=(
            getattr(actor, "tenant_id", None) is None and tenant_key == "default"
        ),
    )
    return row


def update_resource(db: Session, row: AIConfigResource, payload, actor):
    ensure_resource_manageable(
        db,
        resource_type=AI_CONFIG_RESOURCE,
        resource_id=row.id,
        actor=actor,
    )
    if row.config_type not in VALID_CONFIG_TYPES:
        raise HTTPException(status_code=409, detail="legacy_ai_config_is_retired")
    values = payload.model_dump(exclude_unset=True)
    if "resource_key" in values and values["resource_key"] is not None:
        values["resource_key"] = _tenant_resource_key(
            actor_tenant_key(db, actor), values["resource_key"]
        )
        existing = db.query(AIConfigResource).filter(
            AIConfigResource.resource_key == values["resource_key"],
            AIConfigResource.id != row.id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="resource_key already exists")
    next_type = values.get("config_type", row.config_type)
    if next_type != row.config_type:
        raise HTTPException(status_code=400, detail="config_type_is_immutable")
    next_scope_type, next_scope_value = validate_scope(
        values.get("scope_type", row.scope_type),
        values.get("scope_value", row.scope_value),
    )
    next_market_id = values.get("market_id", row.market_id)
    validate_config_shape(next_type, next_scope_type)
    _ensure_singleton_scope_available(
        db,
        config_type=next_type,
        scope_type=next_scope_type,
        scope_value=next_scope_value,
        market_id=next_market_id,
        exclude_id=row.id,
    )
    if "draft_content_json" in values:
        values["draft_content_json"] = validate_agent_config_content(
            next_type, values["draft_content_json"] or {}
        )
    values["scope_type"] = next_scope_type
    values["scope_value"] = next_scope_value
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def publish_resource(
    db: Session,
    row: AIConfigResource,
    actor,
    *,
    notes: Optional[str] = None,
):
    ensure_resource_manageable(
        db,
        resource_type=AI_CONFIG_RESOURCE,
        resource_id=row.id,
        actor=actor,
    )
    if row.config_type not in VALID_CONFIG_TYPES:
        raise HTTPException(status_code=409, detail="legacy_ai_config_is_retired")
    validate_scope(row.scope_type, row.scope_value)
    snapshot = validate_agent_config_content(row.config_type, row.draft_content_json or {})
    new_version = (row.published_version or 0) + 1
    version_row = AIConfigVersion(
        resource_id=row.id,
        version=new_version,
        snapshot_json=snapshot,
        summary=row.draft_summary,
        notes=notes,
        published_by=getattr(actor, "id", None),
    )
    row.draft_content_json = snapshot
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
    ensure_resource_visible(
        db,
        resource_type=AI_CONFIG_RESOURCE,
        resource_id=resource_id,
    )
    return (
        db.query(AIConfigVersion)
        .filter(AIConfigVersion.resource_id == resource_id)
        .order_by(AIConfigVersion.version.desc())
        .all()
    )


def rollback_resource(
    db: Session,
    row: AIConfigResource,
    version: int,
    actor,
    *,
    notes: Optional[str] = None,
):
    ensure_resource_manageable(
        db,
        resource_type=AI_CONFIG_RESOURCE,
        resource_id=row.id,
        actor=actor,
    )
    if row.config_type not in VALID_CONFIG_TYPES:
        raise HTTPException(status_code=409, detail="legacy_ai_config_is_retired")
    target = db.query(AIConfigVersion).filter(
        AIConfigVersion.resource_id == row.id,
        AIConfigVersion.version == version,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="AI config version not found")
    row.draft_content_json = validate_agent_config_content(
        row.config_type, target.snapshot_json or {}
    )
    row.draft_summary = target.summary
    return publish_resource(db, row, actor, notes=notes or f"Rollback to v{version}")


def _ensure_singleton_scope_available(
    db: Session,
    *,
    config_type: str,
    scope_type: str,
    scope_value: str | None,
    market_id: int | None,
    exclude_id: int | None = None,
) -> None:
    if config_type not in SINGLETON_TYPES:
        return
    allowed = manageable_resource_ids(db, resource_type=AI_CONFIG_RESOURCE)
    query = db.query(AIConfigResource).filter(
        AIConfigResource.config_type == config_type,
        AIConfigResource.scope_type == scope_type,
        AIConfigResource.market_id.is_(None)
        if market_id is None
        else AIConfigResource.market_id == market_id,
    )
    if allowed is not None:
        if not allowed:
            return
        query = query.filter(AIConfigResource.id.in_(allowed))
    if scope_value is None:
        query = query.filter(AIConfigResource.scope_value.is_(None))
    else:
        query = query.filter(AIConfigResource.scope_value == scope_value)
    if exclude_id is not None:
        query = query.filter(AIConfigResource.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(status_code=409, detail="singleton_agent_config_scope_conflict")


def _tenant_resource_key(tenant_key: str, value: str) -> str:
    cleaned = normalize_resource_key(value)
    if not cleaned:
        raise HTTPException(status_code=400, detail="resource_key_invalid")
    if tenant_key == "default":
        return cleaned[:120]
    prefix = f"{tenant_key}."
    return (cleaned if cleaned.startswith(prefix) else f"{prefix}{cleaned}")[:120]
