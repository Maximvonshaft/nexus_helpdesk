from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeItem, KnowledgeItemVersion
from ..utils.time import ensure_utc, utc_now

VALID_STATUSES = {"draft", "active", "archived"}
VALID_SOURCE_TYPES = {"text", "url", "file"}


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _validate_shape(*, status: str, source_type: str) -> None:
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported knowledge status")
    if source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported knowledge source_type")


def _has_draft_content(row: KnowledgeItem) -> bool:
    return bool((row.draft_body or "").strip()) or bool((row.draft_normalized_text or "").strip())


def _snapshot(row: KnowledgeItem, *, version: int, published_at) -> dict:
    return {
        "item_key": row.item_key,
        "title": row.title,
        "summary": row.summary,
        "status": row.status,
        "source_type": row.source_type,
        "market_id": row.market_id,
        "channel": row.channel,
        "audience_scope": row.audience_scope,
        "priority": row.priority,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "source_url": row.source_url,
        "file_name": row.file_name,
        "file_storage_key": row.file_storage_key,
        "mime_type": row.mime_type,
        "file_size": row.file_size,
        "body": row.draft_body,
        "normalized_text": row.draft_normalized_text,
        "published_version": version,
        "published_at": published_at.isoformat() if published_at else None,
    }


def list_items(
    db: Session,
    *,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    audience_scope: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[KnowledgeItem], int]:
    query = db.query(KnowledgeItem)
    if status:
        query = query.filter(KnowledgeItem.status == status.strip())
    if source_type:
        query = query.filter(KnowledgeItem.source_type == source_type.strip())
    if market_id is not None:
        query = query.filter(KnowledgeItem.market_id == market_id)
    if channel:
        query = query.filter(KnowledgeItem.channel == channel.strip())
    if audience_scope:
        query = query.filter(KnowledgeItem.audience_scope == audience_scope.strip())
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                KnowledgeItem.item_key.ilike(needle),
                KnowledgeItem.title.ilike(needle),
                KnowledgeItem.summary.ilike(needle),
                KnowledgeItem.draft_normalized_text.ilike(needle),
                KnowledgeItem.published_normalized_text.ilike(needle),
            )
        )
    total = query.count()
    rows = query.order_by(KnowledgeItem.priority.asc(), KnowledgeItem.item_key.asc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return rows, total


def get_item_or_404(db: Session, item_id: int) -> KnowledgeItem:
    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return row


def list_versions(db: Session, item_id: int) -> list[KnowledgeItemVersion]:
    return db.query(KnowledgeItemVersion).filter(KnowledgeItemVersion.item_id == item_id).order_by(KnowledgeItemVersion.version.desc()).all()


def create_item(db: Session, payload, actor) -> KnowledgeItem:
    key = _normalize_key(payload.item_key)
    _validate_shape(status=payload.status, source_type=payload.source_type)
    if db.query(KnowledgeItem).filter(KnowledgeItem.item_key == key).first() is not None:
        raise HTTPException(status_code=409, detail="item_key already exists")
    row = KnowledgeItem(
        item_key=key,
        title=payload.title,
        summary=payload.summary,
        status=payload.status,
        source_type=payload.source_type,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        priority=payload.priority,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        source_url=payload.source_url,
        file_name=payload.file_name,
        file_storage_key=payload.file_storage_key,
        mime_type=payload.mime_type,
        file_size=payload.file_size,
        draft_body=payload.draft_body,
        draft_normalized_text=payload.draft_normalized_text or payload.draft_body,
        created_by=getattr(actor, "id", None),
        updated_by=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()
    return row


def update_item(db: Session, row: KnowledgeItem, payload, actor) -> KnowledgeItem:
    values = payload.model_dump(exclude_unset=True)
    status = values.get("status", row.status)
    source_type = values.get("source_type", row.source_type)
    _validate_shape(status=status, source_type=source_type)
    if values.get("draft_normalized_text") is None and "draft_body" in values:
        values["draft_normalized_text"] = values.get("draft_body")
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.flush()
    return row


def publish_item(db: Session, row: KnowledgeItem, actor, *, notes: Optional[str] = None) -> KnowledgeItemVersion:
    if not _has_draft_content(row):
        raise HTTPException(status_code=400, detail="Draft knowledge content is empty")
    new_version = (row.published_version or 0) + 1
    published_at = utc_now()
    version_row = KnowledgeItemVersion(
        item_id=row.id,
        version=new_version,
        snapshot_json=_snapshot(row, version=new_version, published_at=published_at),
        summary=row.summary,
        notes=notes,
        published_by=getattr(actor, "id", None),
        published_at=published_at,
    )
    row.published_body = row.draft_body
    row.published_normalized_text = row.draft_normalized_text or row.draft_body
    row.published_version = new_version
    row.published_at = published_at
    row.published_by = getattr(actor, "id", None)
    row.updated_by = getattr(actor, "id", None)
    if row.status == "draft":
        row.status = "active"
    db.add(version_row)
    db.flush()
    return version_row


def rollback_item(db: Session, row: KnowledgeItem, *, version: int, actor, notes: Optional[str] = None) -> KnowledgeItemVersion:
    target = db.query(KnowledgeItemVersion).filter(
        KnowledgeItemVersion.item_id == row.id,
        KnowledgeItemVersion.version == version,
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Knowledge item version not found")
    snapshot = target.snapshot_json or {}
    row.title = snapshot.get("title") or row.title
    row.summary = snapshot.get("summary")
    row.status = snapshot.get("status") or row.status
    row.source_type = snapshot.get("source_type") or row.source_type
    row.market_id = snapshot.get("market_id")
    row.channel = snapshot.get("channel")
    row.audience_scope = snapshot.get("audience_scope") or row.audience_scope
    row.priority = snapshot.get("priority") or row.priority
    row.source_url = snapshot.get("source_url")
    row.file_name = snapshot.get("file_name")
    row.file_storage_key = snapshot.get("file_storage_key")
    row.mime_type = snapshot.get("mime_type")
    row.file_size = snapshot.get("file_size")
    row.draft_body = snapshot.get("body")
    row.draft_normalized_text = snapshot.get("normalized_text")
    return publish_item(db, row, actor, notes=notes or f"Rollback to v{version}")


def search_published(
    db: Session,
    *,
    q: Optional[str] = None,
    market_id: Optional[int] = None,
    channel: Optional[str] = None,
    audience_scope: Optional[str] = "customer",
    limit: int = 20,
) -> tuple[list[KnowledgeItem], int]:
    now = utc_now()
    query = db.query(KnowledgeItem).filter(
        KnowledgeItem.status == "active",
        KnowledgeItem.published_version > 0,
        or_(KnowledgeItem.starts_at.is_(None), KnowledgeItem.starts_at <= now),
        or_(KnowledgeItem.ends_at.is_(None), KnowledgeItem.ends_at >= now),
    )
    if market_id is not None:
        query = query.filter(or_(KnowledgeItem.market_id.is_(None), KnowledgeItem.market_id == market_id))
    if channel:
        query = query.filter(or_(KnowledgeItem.channel.is_(None), KnowledgeItem.channel == channel.strip()))
    if audience_scope:
        query = query.filter(KnowledgeItem.audience_scope == audience_scope.strip())
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                KnowledgeItem.item_key.ilike(needle),
                KnowledgeItem.title.ilike(needle),
                KnowledgeItem.summary.ilike(needle),
                KnowledgeItem.published_normalized_text.ilike(needle),
            )
        )
    total = query.count()
    rows = query.order_by(KnowledgeItem.priority.asc(), KnowledgeItem.item_key.asc()).limit(min(max(limit, 1), 100)).all()
    return rows, total
