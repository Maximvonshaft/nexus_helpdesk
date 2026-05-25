from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_control_plane import (
    KnowledgeItemCreate,
    KnowledgeItemDetailOut,
    KnowledgeItemListOut,
    KnowledgeItemOut,
    KnowledgeItemUpdate,
    KnowledgeItemVersionOut,
    KnowledgeChunkHitOut,
    KnowledgePublishRequest,
    KnowledgeRetrievalTestOut,
    KnowledgeRetrievalTestRequest,
    KnowledgeRollbackRequest,
    KnowledgeSearchPublishedOut,
    KnowledgeSearchPublishedRequest,
)
from ..services.permissions import ensure_can_manage_ai_configs, ensure_can_read_ai_configs
from ..services import knowledge_service
from ..services.knowledge_retrieval_service import search_published_chunks
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/knowledge-items", tags=["knowledge-items"])


def _item_out(row) -> KnowledgeItemOut:
    return KnowledgeItemOut.model_validate(row)


def _detail_out(db: Session, row) -> KnowledgeItemDetailOut:
    versions = [KnowledgeItemVersionOut.model_validate(item) for item in knowledge_service.list_versions(db, row.id)]
    return KnowledgeItemDetailOut.model_validate(row).model_copy(update={"versions": versions})


@router.get("", response_model=KnowledgeItemListOut)
def list_knowledge_items(
    status: str | None = None,
    source_type: str | None = None,
    market_id: int | None = None,
    channel: str | None = None,
    audience_scope: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    rows, total = knowledge_service.list_items(
        db,
        status=status,
        source_type=source_type,
        market_id=market_id,
        channel=channel,
        audience_scope=audience_scope,
        q=q,
        limit=limit,
        offset=offset,
    )
    return KnowledgeItemListOut(items=[_item_out(row) for row in rows], total=total)


@router.post("", response_model=KnowledgeItemOut)
def create_knowledge_item(
    payload: KnowledgeItemCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = knowledge_service.create_item(db, payload, current_user)
    db.refresh(row)
    return _item_out(row)


@router.post("/search-published", response_model=KnowledgeSearchPublishedOut)
def search_published_knowledge_items(
    payload: KnowledgeSearchPublishedRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    rows, total = knowledge_service.search_published(
        db,
        q=payload.q,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        limit=payload.limit,
    )
    return KnowledgeSearchPublishedOut(items=[_item_out(row) for row in rows], total=total)


@router.post("/retrieve-test", response_model=KnowledgeRetrievalTestOut)
def test_knowledge_retrieval(
    payload: KnowledgeRetrievalTestRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    hits, total = search_published_chunks(
        db,
        q=payload.q,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        limit=payload.limit,
    )
    return KnowledgeRetrievalTestOut(
        hits=[
            KnowledgeChunkHitOut(
                item_id=hit.item_id,
                item_key=hit.item_key,
                title=hit.title,
                published_version=hit.published_version,
                chunk_index=hit.chunk_index,
                score=hit.score,
                text=hit.text,
                metadata=hit.metadata,
            )
            for hit in hits
        ],
        total=total,
    )


@router.get("/{item_id}", response_model=KnowledgeItemDetailOut)
def get_knowledge_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    row = knowledge_service.get_item_or_404(db, item_id)
    return _detail_out(db, row)


@router.patch("/{item_id}", response_model=KnowledgeItemOut)
def update_knowledge_item(
    item_id: int,
    payload: KnowledgeItemUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = knowledge_service.get_item_or_404(db, item_id)
    with managed_session(db):
        row = knowledge_service.update_item(db, row, payload, current_user)
    db.refresh(row)
    return _item_out(row)


@router.post("/{item_id}/upload", response_model=KnowledgeItemOut)
def upload_knowledge_item_document(
    item_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = knowledge_service.get_item_or_404(db, item_id)
    with managed_session(db):
        row = knowledge_service.upload_document(db, row, file, current_user)
    db.refresh(row)
    return _item_out(row)


@router.post("/{item_id}/publish", response_model=KnowledgeItemVersionOut)
def publish_knowledge_item(
    item_id: int,
    payload: KnowledgePublishRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = knowledge_service.get_item_or_404(db, item_id)
    with managed_session(db):
        version_row = knowledge_service.publish_item(db, row, current_user, notes=payload.notes)
    db.refresh(version_row)
    return KnowledgeItemVersionOut.model_validate(version_row)


@router.post("/{item_id}/rollback", response_model=KnowledgeItemVersionOut)
def rollback_knowledge_item(
    item_id: int,
    payload: KnowledgeRollbackRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = knowledge_service.get_item_or_404(db, item_id)
    with managed_session(db):
        version_row = knowledge_service.rollback_item(db, row, version=payload.version, actor=current_user, notes=payload.notes)
    db.refresh(version_row)
    return KnowledgeItemVersionOut.model_validate(version_row)
