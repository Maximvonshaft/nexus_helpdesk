from __future__ import annotations

from app.knowledge_quarantine.lifecycle import record_safety_review
from app.knowledge_quarantine.policy import evaluate_publication_eligibility
from app.models_knowledge_quarantine import KnowledgeIngestionRecord
from app.utils.time import utc_now


def _record(**overrides) -> KnowledgeIngestionRecord:
    values = {
        "knowledge_item_id": 7,
        "tenant_key": "tenant-a",
        "storage_key": "tenant-a/quarantine/edge",
        "content_sha256": "a" * 64,
        "size_bytes": 12,
        "signature_status": "match",
        "lifecycle_status": "review_required",
        "malware_status": "clean",
        "cdr_status": "clean",
        "prompt_risk_status": "clear",
        "source_trust": "internal_reviewed",
        "review_status": "pending",
        "parser_name": "bounded-parser",
        "parser_version": "1",
    }
    values.update(overrides)
    return KnowledgeIngestionRecord(**values)


def test_human_approval_does_not_change_unavailable_scanner_to_approved() -> None:
    record = _record(malware_status="unavailable", cdr_status="unavailable")
    record_safety_review(
        record,
        reviewer_id=3,
        decision="approved",
        source_trust="internal_reviewed",
    )
    assert record.review_status == "approved"
    assert record.lifecycle_status == "review_required"
    assert evaluate_publication_eligibility(record).eligible is False


def test_sanitized_cdr_requires_a_future_derived_artifact_hash_contract() -> None:
    record = _record(
        lifecycle_status="approved",
        cdr_status="sanitized",
        review_status="approved",
        reviewed_by=3,
        reviewed_at=utc_now(),
    )
    result = evaluate_publication_eligibility(record)
    assert result.eligible is False
    assert "quarantine.cdr_not_clean" in result.reasons


def test_untrusted_source_cannot_become_lifecycle_approved() -> None:
    record = _record()
    record_safety_review(
        record,
        reviewer_id=3,
        decision="approved",
        source_trust="untrusted",
    )
    assert record.review_status == "approved"
    assert record.lifecycle_status == "review_required"
