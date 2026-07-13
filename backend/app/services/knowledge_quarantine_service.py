from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..knowledge_quarantine import (
    DisabledMalwareCdrAdapter,
    InspectionResult,
    ParserBoundaryConfig,
    ParserBoundaryResult,
    PromptRiskResult,
    apply_parser_result,
    apply_scanner_result,
    classify_prompt_risk,
    create_quarantined_ingestion,
    evaluate_publication_eligibility,
    is_exact_published_version_eligible,
    mark_parse_started,
    mark_published,
    mark_rolled_back,
    parse_document_in_boundary,
    record_safety_review,
    reject_ingestion,
    request_re_review,
)
from ..models_control_plane import KnowledgeItem
from ..models_knowledge_quarantine import KnowledgeIngestionAuditEvent, KnowledgeIngestionRecord
from ..settings import get_settings
from . import file_service
from .knowledge_document_service import read_upload_bytes


@dataclass(frozen=True)
class QuarantineUploadResult:
    stored: file_service.StoredUpload
    ingestion: KnowledgeIngestionRecord
    parser: ParserBoundaryResult


def _actor_id(actor) -> int | None:  # noqa: ANN001
    value = getattr(actor, "id", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parsed_text_sha256(value: str | None) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _translate_transition_error(exc: ValueError) -> HTTPException:
    detail = str(exc or "knowledge_quarantine_transition_rejected")[:240]
    return HTTPException(status_code=409, detail=detail)


def list_ingestions(db: Session, *, item_id: int) -> list[KnowledgeIngestionRecord]:
    return (
        db.query(KnowledgeIngestionRecord)
        .filter(KnowledgeIngestionRecord.knowledge_item_id == int(item_id))
        .order_by(KnowledgeIngestionRecord.created_at.desc(), KnowledgeIngestionRecord.id.desc())
        .all()
    )


def get_ingestion_or_404(
    db: Session,
    *,
    item_id: int,
    ingestion_id: int,
    for_update: bool = False,
) -> KnowledgeIngestionRecord:
    query = db.query(KnowledgeIngestionRecord).filter(
        KnowledgeIngestionRecord.id == int(ingestion_id),
        KnowledgeIngestionRecord.knowledge_item_id == int(item_id),
    )
    if for_update:
        query = query.with_for_update()
    record = query.first()
    if record is None:
        raise HTTPException(status_code=404, detail="Knowledge quarantine record not found")
    return record


def get_latest_ingestion(
    db: Session,
    *,
    item_id: int,
    storage_key: str | None = None,
    for_update: bool = False,
) -> KnowledgeIngestionRecord | None:
    query = db.query(KnowledgeIngestionRecord).filter(
        KnowledgeIngestionRecord.knowledge_item_id == int(item_id)
    )
    if storage_key:
        query = query.filter(KnowledgeIngestionRecord.storage_key == storage_key)
    if for_update:
        query = query.with_for_update()
    return query.order_by(
        KnowledgeIngestionRecord.created_at.desc(),
        KnowledgeIngestionRecord.id.desc(),
    ).first()


def list_audit_events(
    db: Session,
    *,
    ingestion_id: int,
) -> list[KnowledgeIngestionAuditEvent]:
    return (
        db.query(KnowledgeIngestionAuditEvent)
        .filter(KnowledgeIngestionAuditEvent.ingestion_id == int(ingestion_id))
        .order_by(KnowledgeIngestionAuditEvent.created_at.asc(), KnowledgeIngestionAuditEvent.id.asc())
        .all()
    )


def quarantine_and_parse_upload(
    db: Session,
    *,
    item: KnowledgeItem,
    file: UploadFile,
    actor,
) -> QuarantineUploadResult:
    """Persist bytes and quarantine authority before invoking any parser."""

    settings = get_settings()
    actor_id = _actor_id(actor)
    content = read_upload_bytes(file)
    content_sha256 = hashlib.sha256(content).hexdigest()
    duplicate = db.query(KnowledgeIngestionRecord.id).filter(
        KnowledgeIngestionRecord.knowledge_item_id == item.id,
        KnowledgeIngestionRecord.content_sha256 == content_sha256,
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Knowledge upload already exists in quarantine")

    # Storage persistence is deliberately before parser execution. The upload
    # stream was rewound by read_upload_bytes; save_upload performs its own
    # extension, MIME and size checks and returns the server-owned storage key.
    stored = file_service.save_upload(file)
    record = create_quarantined_ingestion(
        db,
        item=item,
        storage_key=stored.storage_key,
        original_filename=stored.stored_name,
        content=content,
        declared_mime_type=file.content_type,
        detected_mime_type=stored.mime_type,
        created_by=actor_id,
        max_bytes=settings.max_upload_bytes,
    )
    db.flush()

    if record.signature_status != "match":
        reject_ingestion(
            db,
            record,
            actor_id=actor_id,
            reason="signature.not_verified",
        )
        db.flush()
        return QuarantineUploadResult(
            stored=stored,
            ingestion=record,
            parser=ParserBoundaryResult(
                status="failed",
                parser_identity="nexus.knowledge_document_parser",
                parser_version="v1",
                reason_code="signature.not_verified",
                safe_findings={"signature_status": record.signature_status},
            ),
        )

    try:
        mark_parse_started(db, record, actor_id=actor_id)
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()

    parser = parse_document_in_boundary(
        content=content,
        filename=file.filename,
        mime_type=file.content_type or stored.mime_type,
        config=ParserBoundaryConfig(max_input_bytes=settings.max_upload_bytes),
    )
    prompt_risk = (
        classify_prompt_risk(f"{parser.body or ''}\n{parser.normalized_text or ''}")
        if parser.status == "passed"
        else PromptRiskResult(status="pending", reasons=())
    )
    if parser.status == "passed" and parser.normalized_text:
        record.parsed_text_sha256 = _parsed_text_sha256(parser.normalized_text)
    try:
        apply_parser_result(
            db,
            record,
            parser_status=parser.status,
            parser_identity=parser.parser_identity,
            parser_version=parser.parser_version,
            prompt_risk=prompt_risk,
            safe_findings={
                "reason_code": parser.reason_code,
                **dict(parser.safe_findings or {}),
            },
            actor_id=actor_id,
        )
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc

    if record.lifecycle_status != "rejected":
        disabled_result = DisabledMalwareCdrAdapter().inspect(
            storage_key=record.storage_key,
            content_sha256=record.content_sha256,
            declared_mime_type=record.declared_mime_type,
        )
        try:
            apply_scanner_result(db, record, result=disabled_result, actor_id=actor_id)
        except ValueError as exc:
            raise _translate_transition_error(exc) from exc
    db.flush()
    return QuarantineUploadResult(stored=stored, ingestion=record, parser=parser)


def record_scanner_result(
    db: Session,
    *,
    item_id: int,
    ingestion_id: int,
    result: InspectionResult,
    actor=None,
) -> KnowledgeIngestionRecord:
    """Internal adapter boundary; intentionally not exposed as a public API."""

    record = get_ingestion_or_404(
        db,
        item_id=item_id,
        ingestion_id=ingestion_id,
        for_update=True,
    )
    try:
        apply_scanner_result(db, record, result=result, actor_id=_actor_id(actor))
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()
    return record


def review_ingestion(
    db: Session,
    *,
    item_id: int,
    ingestion_id: int,
    reviewer,
    decision: str,
    source_trust: str,
    reason: str | None = None,
) -> KnowledgeIngestionRecord:
    reviewer_id = _actor_id(reviewer)
    if reviewer_id is None:
        raise HTTPException(status_code=403, detail="Authenticated human reviewer required")
    record = get_ingestion_or_404(
        db,
        item_id=item_id,
        ingestion_id=ingestion_id,
        for_update=True,
    )
    try:
        record_safety_review(
            db,
            record,
            reviewer_id=reviewer_id,
            decision=decision,
            source_trust=source_trust,
            reason=reason,
        )
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()
    return record


def reject_record(
    db: Session,
    *,
    item_id: int,
    ingestion_id: int,
    actor,
    reason: str,
) -> KnowledgeIngestionRecord:
    record = get_ingestion_or_404(
        db,
        item_id=item_id,
        ingestion_id=ingestion_id,
        for_update=True,
    )
    try:
        reject_ingestion(db, record, actor_id=_actor_id(actor), reason=reason)
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()
    return record


def request_record_re_review(
    db: Session,
    *,
    item_id: int,
    ingestion_id: int,
    actor,
    reason: str | None = None,
) -> KnowledgeIngestionRecord:
    actor_id = _actor_id(actor)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="Authenticated human reviewer required")
    record = get_ingestion_or_404(
        db,
        item_id=item_id,
        ingestion_id=ingestion_id,
        for_update=True,
    )
    try:
        request_re_review(db, record, actor_id=actor_id, reason=reason)
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()
    return record


def require_publication_eligible_record(
    db: Session,
    *,
    item: KnowledgeItem,
) -> KnowledgeIngestionRecord:
    if item.source_type != "file":
        raise HTTPException(status_code=409, detail="Knowledge quarantine is only authoritative for file uploads")
    record = get_latest_ingestion(
        db,
        item_id=item.id,
        storage_key=item.file_storage_key,
        for_update=True,
    )
    if record is None:
        raise HTTPException(status_code=409, detail="Knowledge quarantine approval required")
    eligibility = evaluate_publication_eligibility(record)
    if not eligibility.eligible:
        raise HTTPException(status_code=409, detail="Knowledge quarantine approval required")
    current_hash = _parsed_text_sha256(item.draft_normalized_text or item.draft_body)
    if not record.parsed_text_sha256 or current_hash != record.parsed_text_sha256:
        raise HTTPException(
            status_code=409,
            detail="Knowledge quarantine approval invalidated by content change",
        )
    return record


def bind_exact_published_version(
    db: Session,
    *,
    record: KnowledgeIngestionRecord,
    version: int,
    actor,
) -> None:
    try:
        mark_published(
            db,
            record,
            version=int(version),
            actor_id=_actor_id(actor),
        )
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()


def revoke_exact_published_version(
    db: Session,
    *,
    item: KnowledgeItem,
    actor,
    reason: str | None = None,
) -> KnowledgeIngestionRecord | None:
    if item.source_type != "file" or not item.published_version:
        return None
    record = (
        db.query(KnowledgeIngestionRecord)
        .filter(
            KnowledgeIngestionRecord.knowledge_item_id == item.id,
            KnowledgeIngestionRecord.published_version == item.published_version,
            KnowledgeIngestionRecord.lifecycle_status == "published",
        )
        .with_for_update()
        .first()
    )
    if record is None:
        return None
    try:
        mark_rolled_back(
            db,
            record,
            actor_id=_actor_id(actor),
            reason=reason,
        )
    except ValueError as exc:
        raise _translate_transition_error(exc) from exc
    db.flush()
    return record


def is_file_version_retrieval_eligible(
    db: Session,
    *,
    item_id: int,
    version: int,
) -> bool:
    record = db.query(KnowledgeIngestionRecord).filter(
        KnowledgeIngestionRecord.knowledge_item_id == int(item_id),
        KnowledgeIngestionRecord.published_version == int(version),
    ).first()
    return bool(record and is_exact_published_version_eligible(record, version=int(version)))
