from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Ticket
from ..models_case_evidence import CaseEvidenceRecord
from ..models_case_governance import CaseOutcomeRecord
from ..utils.time import ensure_utc, utc_now

EVIDENCE_KINDS = frozenset({"fact", "customer_input"})
EVIDENCE_STATES = frozenset({"verified", "completed", "waived", "failed"})
OUTCOME_TYPES = frozenset(
    {
        "action_intent",
        "execution_attempt",
        "provider_receipt",
        "operational_outcome",
        "customer_notification",
        "closure_assessment",
    }
)
OUTCOME_STATES = frozenset(
    {
        "requested",
        "accepted",
        "processing",
        "succeeded",
        "failed",
        "waived",
        "delivered",
        "confirmed",
        "repair_required",
        "blocked",
        "eligible",
        "closed",
        "reopened",
    }
)
SENSITIVE_KEYS = frozenset(
    {
        "body",
        "content",
        "message",
        "prompt",
        "email",
        "phone",
        "address",
        "contact",
        "recipient",
        "token",
        "authorization",
        "cookie",
        "password",
        "secret",
        "api_key",
        "raw_payload",
        "provider_payload",
    }
)
MAX_PAYLOAD_BYTES = 16_384


@dataclass(frozen=True)
class CaseLedgerProjection:
    fact_classes: frozenset[str]
    customer_inputs: frozenset[str]
    action_classes: frozenset[str]
    outcome_levels: frozenset[str]
    notification_state: str | None
    repair_required: bool
    evidence_record_ids: tuple[int, ...]
    outcome_record_ids: tuple[int, ...]
    latest_material_at: datetime | None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized(value: Any, *, limit: int, code: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(code)
    return text[:limit]


def _safe_payload(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > 4:
        return {"truncated": True, "reason": "depth"}
    normalized_key = key.strip().lower()
    if (
        normalized_key in SENSITIVE_KEYS
        or any(marker in normalized_key for marker in ("token", "secret", "password"))
    ):
        raw = "" if value is None else str(value)
        return {
            "redacted": True,
            "length": len(raw),
            "sha256_prefix": hashlib.sha256(
                raw.encode("utf-8", errors="ignore")
            ).hexdigest()[:16],
        }
    if isinstance(value, dict):
        return {
            str(item_key)[:80]: _safe_payload(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_payload(item, key=key, depth=depth + 1)
            for item in list(value)[:40]
        ]
    if isinstance(value, str):
        if len(value) <= 240:
            return value
        return {
            "truncated": True,
            "length": len(value),
            "sha256_prefix": hashlib.sha256(
                value.encode("utf-8", errors="ignore")
            ).hexdigest()[:16],
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:240]


def sanitize_case_ledger_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    safe = _safe_payload(payload or {})
    if not isinstance(safe, dict):
        raise ValueError("case_ledger_payload_invalid")
    if len(_canonical_json(safe).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("case_ledger_payload_too_large")
    return safe


def _lock_ticket(db: Session, ticket_id: int) -> Ticket:
    query = db.query(Ticket).filter(Ticket.id == ticket_id)
    if db.bind is not None and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    ticket = query.first()
    if ticket is None:
        raise ValueError("case_ticket_not_found")
    return ticket


def record_case_evidence(
    db: Session,
    *,
    ticket_id: int,
    evidence_kind: str,
    evidence_key: str,
    state: str,
    source_kind: str,
    source_ref: str,
    source_revision: str,
    observed_at: datetime,
    recorded_by: int | None,
    safe_metadata: dict[str, Any] | None = None,
) -> tuple[CaseEvidenceRecord, bool]:
    kind = _normalized(
        evidence_kind,
        limit=24,
        code="case_evidence_kind_required",
    ).lower()
    key = _normalized(
        evidence_key,
        limit=160,
        code="case_evidence_key_required",
    ).lower()
    normalized_state = _normalized(
        state,
        limit=24,
        code="case_evidence_state_required",
    ).lower()
    normalized_source = _normalized(
        source_kind,
        limit=80,
        code="case_evidence_source_required",
    ).lower()
    normalized_ref = _normalized(
        source_ref,
        limit=200,
        code="case_evidence_source_ref_required",
    )
    normalized_revision = _normalized(
        source_revision,
        limit=160,
        code="case_evidence_source_revision_required",
    )
    if kind not in EVIDENCE_KINDS:
        raise ValueError("case_evidence_kind_invalid")
    if normalized_state not in EVIDENCE_STATES:
        raise ValueError("case_evidence_state_invalid")
    observed = ensure_utc(observed_at)
    if observed is None:
        raise ValueError("case_evidence_observed_at_required")
    metadata = sanitize_case_ledger_payload(safe_metadata)
    identity = {
        "schema": "nexus.case-evidence.v1",
        "ticket_id": ticket_id,
        "evidence_kind": kind,
        "evidence_key": key,
        "state": normalized_state,
        "source_kind": normalized_source,
        "source_ref": normalized_ref,
        "source_revision": normalized_revision,
        "observed_at": observed.isoformat(),
        "safe_metadata": metadata,
    }
    digest = _sha256(identity)
    existing = (
        db.query(CaseEvidenceRecord)
        .filter(
            CaseEvidenceRecord.ticket_id == ticket_id,
            CaseEvidenceRecord.evidence_key == key,
            CaseEvidenceRecord.source_kind == normalized_source,
            CaseEvidenceRecord.source_ref == normalized_ref,
            CaseEvidenceRecord.source_revision == normalized_revision,
        )
        .first()
    )
    if existing is not None:
        if existing.evidence_sha256 != digest:
            raise ValueError("case_evidence_idempotency_conflict")
        return existing, False

    _lock_ticket(db, ticket_id)
    row = CaseEvidenceRecord(
        ticket_id=ticket_id,
        evidence_kind=kind,
        evidence_key=key,
        state=normalized_state,
        source_kind=normalized_source,
        source_ref=normalized_ref,
        source_revision=normalized_revision,
        evidence_sha256=digest,
        safe_metadata_json=metadata,
        observed_at=observed,
        recorded_by=recorded_by,
        created_at=utc_now(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        existing = (
            db.query(CaseEvidenceRecord)
            .filter(
                CaseEvidenceRecord.ticket_id == ticket_id,
                CaseEvidenceRecord.evidence_key == key,
                CaseEvidenceRecord.source_kind == normalized_source,
                CaseEvidenceRecord.source_ref == normalized_ref,
                CaseEvidenceRecord.source_revision == normalized_revision,
            )
            .first()
        )
        if existing is not None and existing.evidence_sha256 == digest:
            return existing, False
        raise ValueError("case_evidence_concurrent_conflict") from exc
    return row, True


def append_case_outcome(
    db: Session,
    *,
    ticket_id: int,
    record_type: str,
    state: str,
    idempotency_key: str,
    occurred_at: datetime,
    created_by: int | None,
    payload: dict[str, Any] | None = None,
    parent_record_id: int | None = None,
    source_kind: str | None = None,
    source_id: str | None = None,
) -> tuple[CaseOutcomeRecord, bool]:
    normalized_type = _normalized(
        record_type,
        limit=40,
        code="case_outcome_type_required",
    ).lower()
    normalized_state = _normalized(
        state,
        limit=40,
        code="case_outcome_state_required",
    ).lower()
    key = _normalized(
        idempotency_key,
        limit=180,
        code="case_outcome_idempotency_required",
    )
    if normalized_type not in OUTCOME_TYPES:
        raise ValueError("case_outcome_type_invalid")
    if normalized_state not in OUTCOME_STATES:
        raise ValueError("case_outcome_state_invalid")
    occurred = ensure_utc(occurred_at)
    if occurred is None:
        raise ValueError("case_outcome_occurred_at_required")
    safe = sanitize_case_ledger_payload(payload)

    existing = (
        db.query(CaseOutcomeRecord)
        .filter(
            CaseOutcomeRecord.ticket_id == ticket_id,
            CaseOutcomeRecord.idempotency_key == key,
        )
        .first()
    )
    if existing is not None:
        expected = _sha256(
            {
                "record_type": normalized_type,
                "state": normalized_state,
                "payload": safe,
                "parent_record_id": parent_record_id,
                "source_kind": source_kind,
                "source_id": source_id,
                "occurred_at": occurred.isoformat(),
            }
        )
        actual = _sha256(
            {
                "record_type": existing.record_type,
                "state": existing.state,
                "payload": existing.payload_json,
                "parent_record_id": existing.parent_record_id,
                "source_kind": existing.source_kind,
                "source_id": existing.source_id,
                "occurred_at": ensure_utc(existing.occurred_at).isoformat(),
            }
        )
        if expected != actual:
            raise ValueError("case_outcome_idempotency_conflict")
        return existing, False

    _lock_ticket(db, ticket_id)
    if parent_record_id is not None:
        parent = db.get(CaseOutcomeRecord, parent_record_id)
        if parent is None or parent.ticket_id != ticket_id:
            raise ValueError("case_outcome_parent_invalid")
    sequence = int(
        db.query(func.max(CaseOutcomeRecord.sequence))
        .filter(CaseOutcomeRecord.ticket_id == ticket_id)
        .scalar()
        or 0
    ) + 1
    row = CaseOutcomeRecord(
        ticket_id=ticket_id,
        sequence=sequence,
        record_type=normalized_type,
        state=normalized_state,
        idempotency_key=key,
        parent_record_id=parent_record_id,
        source_kind=(str(source_kind).strip().lower()[:80] if source_kind else None),
        source_id=(str(source_id).strip()[:180] if source_id else None),
        payload_json=safe,
        occurred_at=occurred,
        created_by=created_by,
        created_at=utc_now(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        existing = (
            db.query(CaseOutcomeRecord)
            .filter(
                CaseOutcomeRecord.ticket_id == ticket_id,
                CaseOutcomeRecord.idempotency_key == key,
            )
            .first()
        )
        if existing is not None:
            return existing, False
        raise ValueError("case_outcome_concurrent_conflict") from exc
    return row, True


def project_case_ledger(db: Session, *, ticket_id: int) -> CaseLedgerProjection:
    evidence_rows = (
        db.query(CaseEvidenceRecord)
        .filter(CaseEvidenceRecord.ticket_id == ticket_id)
        .order_by(CaseEvidenceRecord.observed_at.asc(), CaseEvidenceRecord.id.asc())
        .all()
    )
    outcome_rows = (
        db.query(CaseOutcomeRecord)
        .filter(CaseOutcomeRecord.ticket_id == ticket_id)
        .order_by(CaseOutcomeRecord.sequence.asc())
        .all()
    )

    facts: set[str] = set()
    inputs: set[str] = set()
    actions: set[str] = set()
    outcomes: set[str] = set()
    notification: str | None = None
    repair_required = False
    latest: datetime | None = None

    for row in evidence_rows:
        observed = ensure_utc(row.observed_at)
        if observed is not None and (latest is None or observed > latest):
            latest = observed
        if row.state == "failed":
            repair_required = True
            continue
        if row.state not in {"verified", "completed", "waived"}:
            continue
        if row.evidence_kind == "fact":
            facts.add(row.evidence_key)
        elif row.evidence_kind == "customer_input":
            inputs.add(row.evidence_key)

    for row in outcome_rows:
        observed = ensure_utc(row.occurred_at)
        if observed is not None and (latest is None or observed > latest):
            latest = observed
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
        if row.state in {"failed", "repair_required"}:
            repair_required = True
        action_class = str(payload.get("action_class") or "").strip().lower()
        outcome_level = str(payload.get("outcome_level") or "").strip().lower()
        if row.record_type in {"execution_attempt", "provider_receipt"}:
            if row.state in {"accepted", "succeeded", "delivered", "confirmed"}:
                if action_class:
                    actions.add(action_class)
                if row.state in {"succeeded", "delivered", "confirmed"}:
                    outcomes.add("technical_completed")
        if row.record_type == "operational_outcome" and row.state in {
            "succeeded",
            "delivered",
            "confirmed",
        }:
            if action_class:
                actions.add(action_class)
            if outcome_level:
                outcomes.add(outcome_level)
        if row.record_type == "customer_notification":
            if row.state == "waived":
                reason = str(payload.get("waiver_reason") or "").strip().lower()
                notification = f"waived:{reason}" if reason else "waived"
            elif row.state in {"succeeded", "delivered", "confirmed"}:
                actions.add("notify_customer")
                outcomes.add("customer_notified")
                notification = "delivered" if row.state in {"delivered", "confirmed"} else "sent"

    return CaseLedgerProjection(
        fact_classes=frozenset(facts),
        customer_inputs=frozenset(inputs),
        action_classes=frozenset(actions),
        outcome_levels=frozenset(outcomes),
        notification_state=notification,
        repair_required=repair_required,
        evidence_record_ids=tuple(row.id for row in evidence_rows),
        outcome_record_ids=tuple(row.id for row in outcome_rows),
        latest_material_at=latest,
    )
