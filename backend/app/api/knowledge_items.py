from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_control_plane import (
    KnowledgeItemCreate,
    KnowledgeItemDetailOut,
    KnowledgeItemListOut,
    KnowledgeItemOut,
    KnowledgeItemUpdate,
    KnowledgeItemVersionOut,
    KnowledgeConflictCheckOut,
    KnowledgeConflictCheckRequest,
    KnowledgeChunkHitOut,
    KnowledgeGoldenTestOut,
    KnowledgeGoldenTestRequest,
    KnowledgePublishRequest,
    KnowledgeRetrievalTestOut,
    KnowledgeRetrievalTestRequest,
    KnowledgeRollbackRequest,
    KnowledgeRuntimeContextTestOut,
    KnowledgeRuntimeContextTestRequest,
    KnowledgeSearchPublishedOut,
    KnowledgeSearchPublishedRequest,
)
from ..services.permissions import ensure_can_manage_ai_configs, ensure_can_read_ai_configs
from ..services import knowledge_service
from ..services.knowledge_studio_service import run_conflict_check
from ..services.knowledge_retrieval_service import retrieve_published_chunks
from ..services.ai_runtime_context import build_agent_context
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/api/knowledge-items", tags=["knowledge-items"])


def _item_out(row) -> KnowledgeItemOut:
    return KnowledgeItemOut.model_validate(row)


def _detail_out(db: Session, row) -> KnowledgeItemDetailOut:
    versions = [KnowledgeItemVersionOut.model_validate(item) for item in knowledge_service.list_versions(db, row.id)]
    return KnowledgeItemDetailOut.model_validate(row).model_copy(update={"versions": versions})


def _retrieval_out(retrieval) -> KnowledgeRetrievalTestOut:
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
                retrieval_method=hit.retrieval_method,
                matched_terms=hit.matched_terms,
                score_breakdown=hit.score_breakdown,
                direct_answer=hit.direct_answer,
                answer_mode=hit.answer_mode,
                source_metadata=hit.source_metadata,
                metadata=hit.metadata,
            )
            for hit in retrieval.hits
        ],
        total=retrieval.total,
        query_analysis=retrieval.query_analysis.as_trace(),
        candidate_count=retrieval.candidate_count,
        top_hits=retrieval.top_hits,
        grounding_would_apply=retrieval.grounding_would_apply,
        grounding_source=retrieval.grounding_source,
    )


@router.get("", response_model=KnowledgeItemListOut)
def list_knowledge_items(
    status: str | None = None,
    source_type: str | None = None,
    knowledge_kind: str | None = None,
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
        knowledge_kind=knowledge_kind,
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


@router.post("/upload", response_model=KnowledgeItemOut)
def create_knowledge_item_from_upload(
    file: UploadFile = File(...),
    item_key: str | None = Form(default=None),
    title: str | None = Form(default=None),
    market_id: int | None = Form(default=None),
    channel: str | None = Form(default="website"),
    audience_scope: str | None = Form(default="customer"),
    language: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = knowledge_service.create_file_item_from_upload(
            db,
            file=file,
            actor=current_user,
            item_key=item_key,
            title=title,
            market_id=market_id,
            channel=channel,
            audience_scope=audience_scope or "customer",
            language=language,
        )
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
    retrieval = retrieve_published_chunks(
        db,
        q=payload.q,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        language=payload.language,
        limit=payload.limit,
    )
    return _retrieval_out(retrieval)


@router.post("/conflict-check", response_model=KnowledgeConflictCheckOut)
def check_knowledge_conflicts(
    payload: KnowledgeConflictCheckRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    return run_conflict_check(db, payload)


@router.post("/golden-test", response_model=KnowledgeGoldenTestOut)
def run_knowledge_golden_test(
    payload: KnowledgeGoldenTestRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    retrieval = retrieve_published_chunks(
        db,
        q=payload.q,
        market_id=payload.market_id,
        channel=payload.channel,
        audience_scope=payload.audience_scope,
        language=payload.language,
        limit=payload.limit,
    )
    passed, assertions = knowledge_service.evaluate_golden_test(payload, retrieval)
    return KnowledgeGoldenTestOut(
        generated_at=utc_now(),
        passed=passed,
        query=payload.q,
        expected_item_key=payload.expected_item_key,
        assertions=assertions,
        retrieval=_retrieval_out(retrieval),
    )


@router.post("/runtime-context-test", response_model=KnowledgeRuntimeContextTestOut)
def test_knowledge_runtime_context(
    payload: KnowledgeRuntimeContextTestRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    return KnowledgeRuntimeContextTestOut(
        context=build_agent_context(
            db,
            tenant_key=payload.tenant_key,
            channel_key=payload.channel or "website",
            body=payload.q,
            market_id=payload.market_id,
            language=payload.language,
            audience_scope=payload.audience_scope or "customer",
        )
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
