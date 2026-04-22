from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Market
from ..models_control_plane import KnowledgeItem, KnowledgeItemVersion

VALID_KNOWLEDGE_STATUS = {"draft", "published", "archived"}
VALID_KNOWLEDGE_SOURCE_TYPES = {"text", "url", "file"}
VALID_AUDIENCE_SCOPES = {"customer", "internal"}


def normalize_key(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)


def normalize_channel(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned or None


def normalize_body(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def normalize_text_for_retrieval(value: str | None) -> str | None:
    cleaned = normalize_body(value)
    if not cleaned:
        return None
    return " ".join(cleaned.split())


def _ensure_market_exists(db: Session, market_id: int | None) -> None:
    if market_id is None:
        return
    if db.query(Market.id).filter(Market.id == market_id, Market.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=400, detail="Market not found or inactive")


def create_item(db: Session, payload: Any, actor_id: int | None) -> KnowledgeItem:
    item_key = normalize_key(payload.item_key)
    if db.query(KnowledgeItem.id).filter(KnowledgeItem.item_key == item_key).first():
        raise HTTPException(status_code=409, detail="item_key already exists")
    if payload.source_type not in VALID_KNOWLEDGE_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported source_type")
    if payload.audience_scope not in VALID_AUDIENCE_SCOPES:
        raise HTTPException(status_code=400, detail="Unsupported audience_scope")
    _ensure_market_exists(db, payload.market_id)
    row = KnowledgeItem(
        item_key=item_key,
        title=payload.title.strip(),
        summary=normalize_body(payload.summary),
        status=payload.status if payload.status in VALID_KNOWLEDGE_STATUS else "draft",
        source_type=payload.source_type,
        market_id=payload.market_id,
        channel=normalize_channel(payload.channel),
        audience_scope=payload.audience_scope,
        priority=payload.priority,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        source_url=normalize_body(payload.source_url),
        file_name=normalize_body(payload.file_name),
        file_storage_key=normalize_body(payload.file_storage_key),
        mime_type=normalize_body(payload.mime_type),
        file_size=payload.file_size,
        draft_body=normalize_body(payload.draft_body),
        draft_normalized_text=normalize_text_for_retrieval(payload.draft_body),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def update_item(db: Session, row: KnowledgeItem, payload: Any, actor_id: int | None) -> KnowledgeItem:
    values = payload.model_dump(exclude_unset=True)
    if "item_key" in values and values["item_key"] is not None:
        values["item_key"] = normalize_key(values["item_key"])
        existing = db.query(KnowledgeItem.id).filter(KnowledgeItem.item_key == values["item_key"], KnowledgeItem.id != row.id).first()
        if existing:
            raise HTTPException(status_code=409, detail="item_key already exists")
    if "market_id" in values:
        _ensure_market_exists(db, values["market_id"])
    if "source_type" in values and values["source_type"] not in VALID_KNOWLEDGE_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported source_type")
    if "audience_scope" in values and values["audience_scope"] not in VALID_AUDIENCE_SCOPES:
        raise HTTPException(status_code=400, detail="Unsupported audience_scope")
    if "status" in values and values["status"] not in VALID_KNOWLEDGE_STATUS:
        raise HTTPException(status_code=400, detail="Unsupported knowledge status")
    for text_field in ("title", "summary", "channel", "source_url", "file_name", "file_storage_key", "mime_type", "draft_body"):
        if text_field in values:
            if text_field == "title" and values[text_field] is not None:
                values[text_field] = values[text_field].strip()
            elif text_field == "channel":
                values[text_field] = normalize_channel(values[text_field])
            else:
                values[text_field] = normalize_body(values[text_field])
    if "draft_body" in values:
        values["draft_normalized_text"] = normalize_text_for_retrieval(values["draft_body"])
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = actor_id
    db.flush()
    return row


def publish_item(db: Session, row: KnowledgeItem, actor_id: int | None, *, notes: str | None = None) -> KnowledgeItemVersion:
    if row.status == "archived":
        raise HTTPException(status_code=400, detail="Archived knowledge item cannot be published")
    if row.source_type == "url" and not row.source_url:
        raise HTTPException(status_code=400, detail="URL source requires source_url")
    if row.source_type == "file" and not row.file_storage_key:
        raise HTTPException(status_code=400, detail="File source requires uploaded file metadata")
    if not row.draft_body:
        raise HTTPException(status_code=400, detail="Knowledge body is empty")
    snapshot = {
        "title": row.title,
        "summary": row.summary,
        "status": "published",
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
    }
    version_num = (row.published_version or 0) + 1
    version = KnowledgeItemVersion(
        item_id=row.id,
        version=version_num,
        snapshot_json=snapshot,
        summary=row.summary,
        notes=normalize_body(notes),
        published_by=actor_id,
    )
    row.status = "published"
    row.published_body = row.draft_body
    row.published_normalized_text = row.draft_normalized_text
    row.published_version = version_num
    row.published_by = actor_id
    row.published_at = version.published_at
    row.updated_by = actor_id
    db.add(version)
    db.flush()
    return version


def list_versions(db: Session, item_id: int) -> list[KnowledgeItemVersion]:
    return db.query(KnowledgeItemVersion).filter(KnowledgeItemVersion.item_id == item_id).order_by(KnowledgeItemVersion.version.desc()).all()


def rollback_item(db: Session, row: KnowledgeItem, version_num: int, actor_id: int | None, *, notes: str | None = None) -> KnowledgeItemVersion:
    target = db.query(KnowledgeItemVersion).filter(KnowledgeItemVersion.item_id == row.id, KnowledgeItemVersion.version == version_num).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Knowledge version not found")
    row.summary = target.snapshot_json.get("summary")
    row.source_type = target.snapshot_json.get("source_type") or row.source_type
    row.market_id = target.snapshot_json.get("market_id")
    row.channel = target.snapshot_json.get("channel")
    row.audience_scope = target.snapshot_json.get("audience_scope") or row.audience_scope
    row.priority = int(target.snapshot_json.get("priority") or row.priority)
    row.source_url = target.snapshot_json.get("source_url")
    row.file_name = target.snapshot_json.get("file_name")
    row.file_storage_key = target.snapshot_json.get("file_storage_key")
    row.mime_type = target.snapshot_json.get("mime_type")
    row.file_size = target.snapshot_json.get("file_size")
    row.draft_body = target.snapshot_json.get("body")
    row.draft_normalized_text = target.snapshot_json.get("normalized_text")
    return publish_item(db, row, actor_id, notes=notes or f"Rollback to v{version_num}")


def list_items(db: Session) -> list[KnowledgeItem]:
    return db.query(KnowledgeItem).order_by(KnowledgeItem.priority.asc(), KnowledgeItem.updated_at.desc()).all()


def resolve_effective_items(
    db: Session,
    *,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    at: datetime | None,
) -> tuple[list[KnowledgeItem], list[str]]:
    when = at or datetime.utcnow()
    reasons = [
        f"market={'global+exact' if market_id is not None else 'global'}",
        f"channel={normalize_channel(channel) or 'all'}",
        f"audience_scope={audience_scope}",
        f"at={when.isoformat()}",
    ]
    query = db.query(KnowledgeItem).filter(
        KnowledgeItem.status == "published",
        KnowledgeItem.published_version > 0,
        KnowledgeItem.audience_scope == audience_scope,
    )
    rows = query.all()
    resolved: list[KnowledgeItem] = []
    normalized_channel = normalize_channel(channel)
    for row in rows:
        if row.market_id is not None and row.market_id != market_id:
            continue
        if row.channel is not None and row.channel != normalized_channel:
            continue
        if row.starts_at and row.starts_at > when:
            continue
        if row.ends_at and row.ends_at < when:
            continue
        resolved.append(row)
    resolved.sort(key=lambda item: (item.priority, -(item.market_id or 0), item.id))
    return resolved, reasons


def read_text_from_storage(file_path: Path | None, mime_type: str | None) -> str | None:
    if file_path is None:
        return None
    normalized_mime = (mime_type or "").lower()
    if normalized_mime not in {"text/plain", "text/markdown", "application/json", "text/csv"}:
        return None
    return file_path.read_text(encoding="utf-8")
