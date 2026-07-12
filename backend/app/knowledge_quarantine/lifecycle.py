from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeItem
from ..models_knowledge_quarantine import KnowledgeIngestionRecord
from ..utils.time import utc_now
from .policy import InspectionResult
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
    "secret",
    "text",
    "token",
    "tracking",
)
_ALLOWED_INSPECTION_STATUSES = {
    "malware": {"unavailable", "pending", "clean", "malicious", "error"},
    "cdr": {"unavailable", "pending", "clean", "sanitized", "rejected", "error"},
    "prompt": {"pending", "clear", "review", "blocked"},
}
_ALLOWED_SOURCE_TRUST = {"untrusted", "internal_unreviewed", "internal_reviewed", "external_verified"}
_TRUSTED_SOURCES = {"internal_reviewed", "external_verified"}


def _bounded_safe_findings(value: Mapping[str, Any] | None) -> dict[str, object]:
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


def create_quarantined_ingestion(
    db: Session,
    *,
    item: KnowledgeItem,
    tenant_key: str,
    storage_key: str,
    content: bytes,
    declared_mime_type: str | None,
    created_by: int | None,
    max_bytes: int = 10 * 1024 * 1024,
) -> KnowledgeIngestionRecord:
    resolved_tenant = str(tenant_key or "").strip()
    if not resolved_tenant or resolved_tenant == "default":
        raise ValueError("knowledge_quarantine_tenant_required")
    if str(item.tenant_id or "").strip() != resolved_tenant:
        raise ValueError("knowledge_quarantine_tenant_mismatch")
    resolved_storage = str(storage_key or "").strip()
    if not resolved_storage or len(resolved_storage) > 255:
        raise ValueError("knowledge_quarantine_storage_key_invalid")
    raw = bytes(content)
    if not raw or len(raw) > max(1, int(max_bytes)):
        raise ValueError("knowledge_quarantine_content_size_invalid")

    signature = evaluate_file_signature(
        declared_mime_type=declared_mime_type,
        prefix=raw[:8192],
    )
    record = KnowledgeIngestionRecord(
        knowledge_item_id=item.id,
        tenant_key=resolved_tenant,
        storage_key=resolved_storage,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        declared_mime_type=signature.declared_mime_type,
        detected_media_type=signature.detected_media_type,
        signature_status=signature.status,
        lifecycle_status="quarantined",
        malware_status="unavailable",
        cdr_status="unavailable",
        prompt_risk_status="pending",
        source_trust="untrusted",
        review_status="pending",
        safe_findings_json={"signature_reason": signature.reason},
        created_by=created_by,
    )
    db.add(record)
    db.flush()
    return record


def apply_inspection_result(
    record: KnowledgeIngestionRecord,
    *,
    result: InspectionResult,
    prompt_risk_status: str,
    parser_name: str,
    parser_version: str,
    safe_findings: Mapping[str, Any] | None = None,
) -> None:
    malware_status = str(result.malware_status or "").strip().lower()
    cdr_status = str(result.cdr_status or "").strip().lower()
    prompt_status = str(prompt_risk_status or "").strip().lower()
    if malware_status not in _ALLOWED_INSPECTION_STATUSES["malware"]:
        raise ValueError("knowledge_quarantine_malware_status_invalid")
    if cdr_status not in _ALLOWED_INSPECTION_STATUSES["cdr"]:
        raise ValueError("knowledge_quarantine_cdr_status_invalid")
    if prompt_status not in _ALLOWED_INSPECTION_STATUSES["prompt"]:
        raise ValueError("knowledge_quarantine_prompt_status_invalid")
    parser = str(parser_name or "").strip()[:80]
    version = str(parser_version or "").strip()[:80]
    if not parser or not version:
        raise ValueError("knowledge_quarantine_parser_identity_required")

    merged_findings = {
        "scanner_identity": str(result.scanner_identity or "unknown")[:80],
        "scanner_reason": str(result.reason or "unknown")[:120],
        **_bounded_safe_findings(result.safe_findings),
        **_bounded_safe_findings(safe_findings),
    }
    record.malware_status = malware_status
    record.cdr_status = cdr_status
    record.prompt_risk_status = prompt_status
    record.parser_name = parser
    record.parser_version = version
    record.safe_findings_json = merged_findings
    record.scanned_at = utc_now()
    record.lifecycle_status = (
        "rejected"
        if malware_status == "malicious" or cdr_status == "rejected" or prompt_status == "blocked"
        else "review_required"
    )


def record_safety_review(
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

    record.review_status = resolved_decision
    record.source_trust = resolved_trust
    record.reviewed_by = reviewer_id
    record.reviewed_at = utc_now()
    record.rejection_reason = str(reason or "").strip()[:120] or None
    if resolved_decision == "rejected":
        record.lifecycle_status = "rejected"
        return

    automated_clear = (
        record.signature_status == "match"
        and record.malware_status == "clean"
        and record.cdr_status == "clean"
        and record.prompt_risk_status == "clear"
        and bool(record.parser_name)
        and bool(record.parser_version)
        and resolved_trust in _TRUSTED_SOURCES
    )
    record.lifecycle_status = "approved" if automated_clear else "review_required"
