from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_control_plane import KnowledgeItem
from ..services.audit_service import log_admin_audit
from ..services.knowledge_service import create_item, list_items, list_versions, publish_item, read_text_from_storage, resolve_effective_items, rollback_item, update_item
from ..services.permissions import ensure_can_manage_ai_configs
from ..services.storage import get_storage_backend
from ..unit_of_work import managed_session
from .deps import get_current_user

ALLOWED_UPLOAD_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "application/json",
    "text/csv",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".pdf", ".doc", ".docx"}


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_dt(self, value: Any):
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class KnowledgeItemRead(APIModel):
    id: int
    item_key: str
    title: str
    summary: str | None = None
    status: str
    source_type: str
    market_id: int | None = None
    channel: str | None = None
    audience_scope: str
    priority: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source_url: str | None = None
    file_name: str | None = None
    file_storage_key: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    draft_body: str | None = None
    published_body: str | None = None
    published_version: int
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeItemCreate(BaseModel):
    item_key: str
    title: str
    summary: str | None = None
    status: str = "draft"
    source_type: str = "text"
    market_id: int | None = None
    channel: str | None = None
    audience_scope: str = "customer"
    priority: int = 100
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source_url: str | None = None
    file_name: str | None = None
    file_storage_key: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    draft_body: str | None = None


class KnowledgeItemUpdate(BaseModel):
    item_key: str | None = None
    title: str | None = None
    summary: str | None = None
    status: str | None = None
    source_type: str | None = None
    market_id: int | None = None
    channel: str | None = None
    audience_scope: str | None = None
    priority: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source_url: str | None = None
    file_name: str | None = None
    file_storage_key: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    draft_body: str | None = None


class PublishRequest(BaseModel):
    notes: str | None = None


class KnowledgeVersionRead(APIModel):
    id: int
    item_id: int
    version: int
    snapshot_json: dict[str, Any]
    summary: str | None = None
    notes: str | None = None
    published_by: int | None = None
    published_at: datetime


class KnowledgePreviewRequest(BaseModel):
    market_id: int | None = None
    channel: str | None = None
    audience_scope: str = "customer"
    at: datetime | None = None


class KnowledgePreviewRead(BaseModel):
    matched_items: list[KnowledgeItemRead]
    debug_steps: list[str] = Field(default_factory=list)


class KnowledgeUploadRead(BaseModel):
    file_name: str
    storage_key: str
    mime_type: str
    size_bytes: int
    extracted_text: str | None = None


router = APIRouter(prefix="/api/admin/knowledge-items", tags=["admin-knowledge"])


@router.get("", response_model=list[KnowledgeItemRead])
def list_knowledge_items(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    return [KnowledgeItemRead.model_validate(row) for row in list_items(db)]


@router.post("", response_model=KnowledgeItemRead)
def create_knowledge_item(payload: KnowledgeItemCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = create_item(db, payload, getattr(current_user, "id", None))
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="knowledge_item.create",
            target_type="knowledge_item",
            target_id=row.id,
            old_value=None,
            new_value={"item_key": row.item_key, "status": row.status, "market_id": row.market_id, "channel": row.channel},
        )
    db.refresh(row)
    return KnowledgeItemRead.model_validate(row)


@router.patch("/{item_id}", response_model=KnowledgeItemRead)
def update_knowledge_item(item_id: int, payload: KnowledgeItemUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    before = {"status": row.status, "market_id": row.market_id, "channel": row.channel, "priority": row.priority}
    with managed_session(db):
        row = update_item(db, row, payload, getattr(current_user, "id", None))
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="knowledge_item.update",
            target_type="knowledge_item",
            target_id=row.id,
            old_value=before,
            new_value={"status": row.status, "market_id": row.market_id, "channel": row.channel, "priority": row.priority},
        )
    db.refresh(row)
    return KnowledgeItemRead.model_validate(row)


@router.post("/{item_id}/publish", response_model=KnowledgeVersionRead)
def publish_knowledge_item(item_id: int, payload: PublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    with managed_session(db):
        version = publish_item(db, row, getattr(current_user, "id", None), notes=payload.notes)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="knowledge_item.publish",
            target_type="knowledge_item",
            target_id=row.id,
            old_value={"published_version": row.published_version - 1, "status": "draft"},
            new_value={"published_version": row.published_version, "status": row.status},
        )
    db.refresh(version)
    return KnowledgeVersionRead.model_validate(version)


@router.post("/{item_id}/archive", response_model=KnowledgeItemRead)
def archive_knowledge_item(item_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    with managed_session(db):
        before = {"status": row.status}
        row.status = "archived"
        db.flush()
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="knowledge_item.archive",
            target_type="knowledge_item",
            target_id=row.id,
            old_value=before,
            new_value={"status": row.status},
        )
    db.refresh(row)
    return KnowledgeItemRead.model_validate(row)


@router.get("/{item_id}/versions", response_model=list[KnowledgeVersionRead])
def get_knowledge_versions(item_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    return [KnowledgeVersionRead.model_validate(row) for row in list_versions(db, item_id)]


@router.post("/{item_id}/rollback/{version_num}", response_model=KnowledgeVersionRead)
def rollback_knowledge_item(item_id: int, version_num: int, payload: PublishRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    with managed_session(db):
        version = rollback_item(db, row, version_num, getattr(current_user, "id", None), notes=payload.notes)
        log_admin_audit(
            db,
            actor_id=getattr(current_user, "id", None),
            action="knowledge_item.rollback",
            target_type="knowledge_item",
            target_id=row.id,
            old_value={"requested_version": version_num - 1},
            new_value={"requested_version": version_num, "published_version": row.published_version},
        )
    db.refresh(version)
    return KnowledgeVersionRead.model_validate(version)


@router.post("/resolve-preview", response_model=KnowledgePreviewRead)
def preview_knowledge_resolution(payload: KnowledgePreviewRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    items, reasons = resolve_effective_items(
        db,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        at=payload.at,
    )
    return KnowledgePreviewRead(matched_items=[KnowledgeItemRead.model_validate(row) for row in items], debug_steps=reasons)


@router.post("/upload", response_model=KnowledgeUploadRead)
def upload_knowledge_source(file: UploadFile = File(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_ai_configs(current_user, db)
    storage = get_storage_backend()
    stored = storage.save_upload(
        file,
        allowed_mime_types=ALLOWED_UPLOAD_MIME_TYPES,
        allowed_extensions=ALLOWED_UPLOAD_EXTENSIONS,
        max_bytes=10 * 1024 * 1024,
    )
    extracted = read_text_from_storage(stored.absolute_path, stored.detected_mime_type)
    log_admin_audit(
        db,
        actor_id=getattr(current_user, "id", None),
        action="knowledge_item.upload_source",
        target_type="knowledge_upload",
        target_id=None,
        old_value=None,
        new_value={"file_name": file.filename, "storage_key": stored.storage_key, "mime_type": stored.detected_mime_type, "size_bytes": stored.size_bytes},
    )
    db.commit()
    return KnowledgeUploadRead(
        file_name=file.filename or stored.storage_key,
        storage_key=stored.storage_key,
        mime_type=stored.detected_mime_type,
        size_bytes=stored.size_bytes,
        extracted_text=extracted,
    )
