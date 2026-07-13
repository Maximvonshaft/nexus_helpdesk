from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeItem
from ..models_knowledge_quarantine import KnowledgeIngestionAuditEvent, KnowledgeIngestionRecord
from ..utils.time import utc_now
from .policy import InspectionResult, PromptRiskResult, evaluate_publication_eligibility, safe_reason
from .signatures import evaluate_file_signature

_SENSITIVE_KEYS = (
    "body",
    "content",
    "credential",
    "customer",
    "email",
    "message",
    "password",
    "payload",
    "phone",
    "prompt",
    "raw",
    "secret",
    "stderr",
    "text",
    "token",
    "tracking",
)
_ALLOWED_SOURCE_TRUST = {"untrusted", "internal_unreviewed", "internal_reviewed", "external_verified"}
_TRUSTED_SOURCE = {"internal_reviewed", "external_verified"}
_ALLOWED_MALWARE = {"unavailable", "pending", "clean", "malicious", "error"}
_ALLOWED_CDR = {"unavailable", "pending", "clean", "sanitized", "rejected", "error"}


def _bounded_safe_metadata(value: Mapping[str, Any] | None) -> dict[str, object]:
    safe: dict[str, object] = {}
    for raw_key, raw_value in list((value or {}).items())[:32]:
        key = str(raw_key or "").strip().lower().replace(" ", "_")[:80]
        if not key or any(token in key for token in _SENSITIVE_KEYS):
            continue
        if raw_value is None or isinstance(raw_value, (bool, int, float)):
            safe[key] = raw_value
        elif isinstance(raw_value, str):
            safe[key] = raw_value[:120]
        elif isinstance(raw_value, (list, tuple)):
            safe[key] = [
                item if item is None or isinstance(item, (bool, int, float)) else str(item)[:80]
                for item in list(raw_value)[:16]
            ]
        else:
            safe[key] = type(raw_value).__name__
    return safe


def _audit(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    actor_id: int | None,
    reason_code: str,
    metadata: Mapping[str, Any] | None = None,
) -> KnowledgeIngestionAuditEvent:
    event = KnowledgeIngestionAuditEvent(
        ingestion_id=record.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        reason_code=safe_reason(reason_code, fallback="audit.reason_invalid"),
        safe_metadata_json=_bounded_safe_metadata(metadata) or None,
    )
    db.add(event)
    db.flush()
    return event


def create_quarantined_ingestion(
    db: Session,
    *,
    item: KnowledgeItem,
    storage_key: str,
    original_filename: str,
    content: bytes,
    declared_mime_type: str | None,
    detected_mime_type: str | None,
    created_by: int | None,
    max_bytes: int,
) -> KnowledgeIngestionRecord:
    if item.id is None:
        raise ValueError("knowledge_quarantine_item_must_be_persisted")
    resolved_storage = str(storage_key or "").strip()
    if not resolved_storage or len(resolved_storage) > 255:
        raise ValueError("knowledge_quarantine_storage_key_invalid")
    filename = str(original_filename or "upload.bin").strip()[:255] or "upload.bin"
    raw = bytes(content)
    if not raw or len(raw) > max(1, int(max_bytes)):
        raise ValueError("knowledge_quarantine_content_size_invalid")

    signature = evaluate_file_signature(
        declared_mime_type=declared_mime_type,
        prefix=raw[:8192],
    )
    if detected_mime_type and signature.detected_mime_type and detected_mime_type != signature.detected_mime_type:
        signature_status = "mismatch"
        signature_reason = "signature.storage_detector_disagrees"
    else:
        signature_status = signature.status
        signature_reason = signature.reason

    record = KnowledgeIngestionRecord(
        knowledge_item_id=item.id,
        storage_key=resolved_storage,
        original_filename=filename,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        declared_mime_type=signature.declared_mime_type,
        detected_mime_type=(detected_mime_type or signature.detected_mime_type or "")[:120] or None,
        signature_status=signature_status,
        lifecycle_status="quarantined",
        parser_status="not_started",
        malware_status="unavailable",
        cdr_status="unavailable",
        prompt_risk_status="pending",
        source_trust="untrusted",
        review_status="pending",
        safe_findings_json={"signature_reason": signature_reason},
        created_by=created_by,
    )
    db.add(record)
    db.flush()
    _audit(
        db,
        record,
        event_type="quarantined",
        from_status=None,
        to_status="quarantined",
        actor_id=created_by,
        reason_code="quarantine.upload_persisted",
        metadata={
            "signature_status": signature_status,
            "detected_mime_type": record.detected_mime_type,
            "size_bytes": record.size_bytes,
        },
    )
    return record


def mark_parse_started(db: Session, record: KnowledgeIngestionRecord, *, actor_id: int | None = None) -> None:
    if record.lifecycle_status != "quarantined" or record.signature_status != "match":
        raise ValueError("knowledge_quarantine_not_parse_eligible")
    previous = record.lifecycle_status
    record.lifecycle_status = "parsing"
    record.parser_status = "running"
    _audit(
        db,
        record,
        event_type="parse_started",
        from_status=previous,
        to_status="parsing",
        actor_id=actor_id,
        reason_code="parser.boundary_started",
    )


def apply_parser_result(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    parser_status: str,
    parser_identity: str,
    parser_version: str,
    prompt_risk: PromptRiskResult,
    safe_findings: Mapping[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    status = str(parser_status or "").strip().lower()
    if status not in {"passed", "failed", "timed_out", "resource_exceeded"}:
        raise ValueError("knowledge_quarantine_parser_status_invalid")
    identity = str(parser_identity or "").strip()[:120]
    version = str(parser_version or "").strip()[:80]
    if not identity or not version:
        raise ValueError("knowledge_quarantine_parser_identity_required")
    if record.lifecycle_status in {"rejected", "rolled_back"}:
        raise ValueError("knowledge_quarantine_terminal_state")

    previous = record.lifecycle_status
    record.parser_status = status
    record.parser_identity = identity
    record.parser_version = version
    record.prompt_risk_status = prompt_risk.status
    merged = dict(record.safe_findings_json or {})
    merged.update(_bounded_safe_metadata(safe_findings))
    merged["prompt_risk_reasons"] = list(prompt_risk.reasons)[:8]
    record.safe_findings_json = _bounded_safe_metadata(merged)

    if status != "passed" or prompt_risk.status == "blocked":
        record.lifecycle_status = "rejected"
        record.review_status = "rejected"
        record.rejection_reason = (
            "prompt_risk.blocked" if prompt_risk.status == "blocked" else f"parser.{status}"
        )
        event_type = "parse_failed"
    else:
        record.lifecycle_status = "review_required"
        event_type = "parse_passed"

    _audit(
        db,
        record,
        event_type=event_type,
        from_status=previous,
        to_status=record.lifecycle_status,
        actor_id=actor_id,
        reason_code=record.rejection_reason or "parser.completed",
        metadata={
            "parser_status": status,
            "parser_identity": identity,
            "parser_version": version,
            "prompt_risk_status": prompt_risk.status,
        },
    )


def apply_scanner_result(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    result: InspectionResult,
    actor_id: int | None = None,
) -> None:
    malware = str(result.malware_status or "").strip().lower()
    cdr = str(result.cdr_status or "").strip().lower()
    if malware not in _ALLOWED_MALWARE:
        raise ValueError("knowledge_quarantine_malware_status_invalid")
    if cdr not in _ALLOWED_CDR:
        raise ValueError("knowledge_quarantine_cdr_status_invalid")
    if record.lifecycle_status in {"rejected", "rolled_back"}:
        raise ValueError("knowledge_quarantine_terminal_state")

    previous = record.lifecycle_status
    record.malware_status = malware
    record.cdr_status = cdr
    record.sanitized_content_sha256 = (
        str(result.sanitized_content_sha256 or "")[:64] or None
    )
    merged = dict(record.safe_findings_json or {})
    merged.update(
        _bounded_safe_metadata(
            {
                "scanner_identity": result.scanner_identity,
                "scanner_reason": result.reason,
                **dict(result.safe_findings or {}),
            }
        )
    )
    record.safe_findings_json = merged
    if malware == "malicious" or cdr == "rejected":
        record.lifecycle_status = "rejected"
        record.review_status = "rejected"
        record.rejection_reason = "scanner.malicious_or_rejected"
    else:
        record.lifecycle_status = "review_required"

    _audit(
        db,
        record,
        event_type="scanner_recorded",
        from_status=previous,
        to_status=record.lifecycle_status,
        actor_id=actor_id,
        reason_code=result.reason,
        metadata={"malware_status": malware, "cdr_status": cdr, "scanner_identity": result.scanner_identity},
    )


def reject_ingestion(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    actor_id: int | None,
    reason: str,
) -> None:
    if record.lifecycle_status == "rolled_back":
        raise ValueError("knowledge_quarantine_terminal_state")
    previous = record.lifecycle_status
    record.lifecycle_status = "rejected"
    record.review_status = "rejected"
    record.rejection_reason = safe_reason(reason, fallback="quarantine.rejected")
    if record.reviewed_at is None:
        record.reviewed_at = utc_now()
    record.reviewed_by = actor_id
    _audit(
        db,
        record,
        event_type="rejected",
        from_status=previous,
        to_status="rejected",
        actor_id=actor_id,
        reason_code=record.rejection_reason,
    )


def record_safety_review(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    reviewer_id: int,
    decision: str,
    source_trust: str,
    reason: str | None = None,
) -> None:
    resolved_decision = str(decision or "").strip().lower()
    resolved_trust = str(source_trust or "").strip().lower()
    if resolved_decision not in {"approved", "rejected"}:
        raise ValueError("knowledge_quarantine_review_decision_invalid")
    if resolved_trust not in _ALLOWED_SOURCE_TRUST:
        raise ValueError("knowledge_quarantine_source_trust_invalid")
    if not isinstance(reviewer_id, int) or isinstance(reviewer_id, bool) or reviewer_id <= 0:
        raise ValueError("knowledge_quarantine_reviewer_required")
    if record.lifecycle_status in {"rejected", "rolled_back"}:
        raise ValueError("knowledge_quarantine_terminal_state")

    previous = record.lifecycle_status
    record.review_status = resolved_decision
    record.source_trust = resolved_trust
    record.reviewed_by = reviewer_id
    record.reviewed_at = utc_now()
    if resolved_decision == "rejected":
        record.lifecycle_status = "rejected"
        record.rejection_reason = safe_reason(reason, fallback="review.rejected")
        event_type = "rejected"
    else:
        automated_clear = (
            record.signature_status == "match"
            and record.parser_status == "passed"
            and record.malware_status == "clean"
            and record.cdr_status == "clean"
            and record.prompt_risk_status == "clear"
            and bool(record.parser_identity)
            and bool(record.parser_version)
            and resolved_trust in _TRUSTED_SOURCE
        )
        record.lifecycle_status = "approved" if automated_clear else "review_required"
        record.rejection_reason = None
        event_type = "review_approved"

    _audit(
        db,
        record,
        event_type=event_type,
        from_status=previous,
        to_status=record.lifecycle_status,
        actor_id=reviewer_id,
        reason_code=reason or f"review.{resolved_decision}",
        metadata={"source_trust": resolved_trust, "review_status": resolved_decision},
    )


def mark_published(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    version: int,
    actor_id: int | None,
) -> None:
    eligibility = evaluate_publication_eligibility(record)
    if not eligibility.eligible:
        raise ValueError("knowledge_quarantine_not_publication_eligible:" + ",".join(eligibility.reasons))
    if int(version) <= 0:
        raise ValueError("knowledge_quarantine_published_version_invalid")
    previous = record.lifecycle_status
    record.lifecycle_status = "published"
    record.published_version = int(version)
    record.published_at = utc_now()
    _audit(
        db,
        record,
        event_type="published",
        from_status=previous,
        to_status="published",
        actor_id=actor_id,
        reason_code="publication.exact_version_bound",
        metadata={"published_version": int(version), "content_sha256": record.content_sha256},
    )


def mark_rolled_back(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    actor_id: int | None,
    reason: str | None = None,
) -> None:
    if record.lifecycle_status != "published" or record.published_version is None:
        raise ValueError("knowledge_quarantine_not_rollback_eligible")
    previous = record.lifecycle_status
    record.lifecycle_status = "rolled_back"
    record.rolled_back_at = utc_now()
    _audit(
        db,
        record,
        event_type="rolled_back",
        from_status=previous,
        to_status="rolled_back",
        actor_id=actor_id,
        reason_code=reason or "publication.rollback",
        metadata={"published_version": record.published_version, "content_sha256": record.content_sha256},
    )


def request_re_review(
    db: Session,
    record: KnowledgeIngestionRecord,
    *,
    actor_id: int,
    reason: str | None = None,
) -> None:
    if record.lifecycle_status not in {"approved", "published", "review_required"}:
        raise ValueError("knowledge_quarantine_re_review_not_allowed")
    previous = record.lifecycle_status
    record.lifecycle_status = "review_required"
    record.review_status = "re_review_required"
    record.reviewed_by = None
    record.reviewed_at = None
    _audit(
        db,
        record,
        event_type="re_review_requested",
        from_status=previous,
        to_status="review_required",
        actor_id=actor_id,
        reason_code=reason or "review.re_review_requested",
        metadata={"published_version": record.published_version},
    )
