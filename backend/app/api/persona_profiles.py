from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas_control_plane import (
    PersonaProfileCreate,
    PersonaProfileDetailOut,
    PersonaProfileListOut,
    PersonaProfileReviewListOut,
    PersonaProfileReviewOut,
    PersonaProfileOut,
    PersonaProfileUpdate,
    PersonaProfileVersionOut,
    PersonaPublishRequest,
    PersonaReviewDecisionRequest,
    PersonaReviewPublishRequest,
    PersonaReviewSubmitRequest,
    PersonaRuntimeEvidenceOut,
    PersonaRuntimeEvidenceRequest,
    PersonaResolvePreviewOut,
    PersonaResolvePreviewRequest,
    PersonaRollbackRequest,
)
from ..services.permissions import ensure_can_manage_ai_configs, ensure_can_read_ai_configs
from ..services import persona_service
from ..services.ai_runtime_context import build_webchat_runtime_context
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/api/persona-profiles", tags=["persona-profiles"])


def _profile_out(row) -> PersonaProfileOut:
    return PersonaProfileOut.model_validate(row)


def _detail_out(db: Session, row) -> PersonaProfileDetailOut:
    versions = [PersonaProfileVersionOut.model_validate(item) for item in persona_service.list_versions(db, row.id)]
    return PersonaProfileDetailOut.model_validate(row).model_copy(update={"versions": versions})


def _review_out(row) -> PersonaProfileReviewOut:
    return PersonaProfileReviewOut.model_validate(row)


@router.get("", response_model=PersonaProfileListOut)
def list_persona_profiles(
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    rows, total = persona_service.list_profiles(
        db,
        market_id=market_id,
        channel=channel,
        language=language,
        is_active=is_active,
        q=q,
        limit=limit,
        offset=offset,
    )
    return PersonaProfileListOut(profiles=[_profile_out(row) for row in rows], total=total)


@router.post("", response_model=PersonaProfileOut)
def create_persona_profile(
    payload: PersonaProfileCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    with managed_session(db):
        row = persona_service.create_profile(db, payload, current_user)
    db.refresh(row)
    return _profile_out(row)


@router.post("/resolve-preview", response_model=PersonaResolvePreviewOut)
def resolve_persona_preview(
    payload: PersonaResolvePreviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    row, score = persona_service.resolve_preview(
        db,
        market_id=payload.market_id,
        channel=payload.channel,
        language=payload.language,
    )
    return PersonaResolvePreviewOut(profile=_profile_out(row) if row else None, match_rank=score)


@router.post("/runtime-evidence", response_model=PersonaRuntimeEvidenceOut)
def get_persona_runtime_evidence(
    payload: PersonaRuntimeEvidenceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    runtime_context = build_webchat_runtime_context(
        db,
        tenant_key=payload.tenant_key,
        channel_key=payload.channel or "webchat",
        body=payload.body,
        market_id=payload.market_id,
        language=payload.language,
        audience_scope=payload.audience_scope or "customer",
    )
    persona_context = runtime_context.get("persona_context")
    matched_profile_key = persona_context.get("profile_key") if isinstance(persona_context, dict) else None
    match_rank = persona_context.get("match_rank") if isinstance(persona_context, dict) else None
    matched_expected = None
    if payload.expected_profile_key:
        matched_expected = matched_profile_key == payload.expected_profile_key
    identity_context = persona_context.get("identity_context") if isinstance(persona_context, dict) else {}
    content_json = persona_context.get("content_json") if isinstance(persona_context, dict) else {}
    return PersonaRuntimeEvidenceOut(
        generated_at=utc_now(),
        matched_profile_key=matched_profile_key,
        match_rank=match_rank,
        expected_profile_key=payload.expected_profile_key,
        matched_expected=matched_expected,
        persona_context=persona_context if isinstance(persona_context, dict) else None,
        runtime_context=runtime_context,
        evidence={
            "runtime_contract": "build_webchat_runtime_context",
            "context_version": runtime_context.get("context_version"),
            "metadata_filters": runtime_context.get("metadata_filters"),
            "identity_ready": bool(identity_context),
            "brand_name": identity_context.get("brand_name") if isinstance(identity_context, dict) else None,
            "assistant_name": identity_context.get("assistant_name") if isinstance(identity_context, dict) else None,
            "guardrail_count": len(content_json.get("guardrails") or []) if isinstance(content_json, dict) and isinstance(content_json.get("guardrails"), list) else 0,
            "knowledge_hits": runtime_context.get("knowledge_context", {}).get("total_matches") if isinstance(runtime_context.get("knowledge_context"), dict) else None,
        },
    )


@router.get("/reviews", response_model=PersonaProfileReviewListOut)
def list_persona_reviews(
    profile_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    rows, total = persona_service.list_reviews(db, profile_id=profile_id, status=status, limit=limit, offset=offset)
    return PersonaProfileReviewListOut(reviews=[_review_out(row) for row in rows], total=total)


@router.post("/reviews/{review_id}/approve", response_model=PersonaProfileReviewOut)
def approve_persona_review(
    review_id: int,
    payload: PersonaReviewDecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    review = persona_service.get_review_or_404(db, review_id)
    with managed_session(db):
        review = persona_service.approve_review(db, review, payload, current_user)
    db.refresh(review)
    return _review_out(review)


@router.post("/reviews/{review_id}/reject", response_model=PersonaProfileReviewOut)
def reject_persona_review(
    review_id: int,
    payload: PersonaReviewDecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    review = persona_service.get_review_or_404(db, review_id)
    with managed_session(db):
        review = persona_service.reject_review(db, review, payload, current_user)
    db.refresh(review)
    return _review_out(review)


@router.post("/reviews/{review_id}/publish", response_model=PersonaProfileVersionOut)
def publish_approved_persona_review(
    review_id: int,
    payload: PersonaReviewPublishRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    review = persona_service.get_review_or_404(db, review_id)
    with managed_session(db):
        version_row = persona_service.publish_approved_review(db, review, current_user, notes=payload.notes)
    db.refresh(version_row)
    return PersonaProfileVersionOut.model_validate(version_row)


@router.get("/{profile_id}", response_model=PersonaProfileDetailOut)
def get_persona_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    return _detail_out(db, row)


@router.patch("/{profile_id}", response_model=PersonaProfileOut)
def update_persona_profile(
    profile_id: int,
    payload: PersonaProfileUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        row = persona_service.update_profile(db, row, payload, current_user)
    db.refresh(row)
    return _profile_out(row)


@router.post("/{profile_id}/submit-review", response_model=PersonaProfileReviewOut)
def submit_persona_review(
    profile_id: int,
    payload: PersonaReviewSubmitRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        review = persona_service.submit_review(db, row, payload, current_user)
    db.refresh(review)
    return _review_out(review)


@router.post("/{profile_id}/publish", response_model=PersonaProfileVersionOut)
def publish_persona_profile(
    profile_id: int,
    payload: PersonaPublishRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        version_row = persona_service.publish_profile(db, row, current_user, notes=payload.notes)
    db.refresh(version_row)
    return PersonaProfileVersionOut.model_validate(version_row)


@router.post("/{profile_id}/rollback", response_model=PersonaProfileVersionOut)
def rollback_persona_profile(
    profile_id: int,
    payload: PersonaRollbackRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_ai_configs(current_user, db)
    row = persona_service.get_profile_or_404(db, profile_id)
    with managed_session(db):
        version_row = persona_service.rollback_profile(db, row, version=payload.version, actor=current_user, notes=payload.notes)
    db.refresh(version_row)
    return PersonaProfileVersionOut.model_validate(version_row)
