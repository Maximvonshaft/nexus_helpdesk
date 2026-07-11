from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import Ticket
from ..models_osr import CaseContextRecord
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatConversation
from .tracking_fact_schema import (
    EVIDENCE_AVAILABLE,
    FRESHNESS_FRESH,
    SOURCE_AUTHORITY_PRIMARY,
    hash_tracking_number,
)

MAX_SERVER_FACT_AGE = timedelta(minutes=30)
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_TERMINAL_CONTEXT_STATUSES = {"closed", "archived"}


@dataclass(frozen=True)
class ServerFactEvidence:
    present: bool
    reason: str
    reference_id: int | None = None
    source: str | None = None
    tool_name: str | None = None
    authority: str | None = None
    evidence_state: str | None = None
    freshness: str | None = None
    checked_at: str | None = None
    observed_at: str | None = None
    tracking_number_hash: str | None = None
    safe_tracking_reference: str | None = None

    def audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = (text.replace("Z", "+00:00"), text)
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            try:
                parsed = datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _fact_mapping(row: CaseContextRecord) -> dict[str, Any]:
    raw = row.last_mcp_fact_json
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("tracking_fact")
    if isinstance(nested, dict):
        return {**raw, **nested}
    return dict(raw)


def _bounded_label(value: Any, *, limit: int = 120) -> str | None:
    text = " ".join(str(value or "").strip().split())
    return text[:limit] if text else None


def _denied(reason: str, row: CaseContextRecord | None = None, fact: dict[str, Any] | None = None) -> ServerFactEvidence:
    fact = fact or {}
    return ServerFactEvidence(
        present=False,
        reason=reason,
        reference_id=row.id if row is not None else None,
        source=_bounded_label(fact.get("fact_source") or fact.get("source")),
        tool_name=_bounded_label(fact.get("tool_name")),
        authority=_bounded_label(fact.get("source_authority")),
        evidence_state=_bounded_label(fact.get("evidence_state")),
        freshness=_bounded_label(fact.get("freshness")),
        checked_at=_bounded_label(fact.get("checked_at"), limit=64),
        observed_at=_bounded_label(fact.get("observed_at"), limit=64),
        tracking_number_hash=_bounded_label(fact.get("tracking_number_hash"), limit=96),
        safe_tracking_reference=_bounded_label(fact.get("safe_tracking_reference"), limit=80),
    )


def resolve_server_fact_evidence(
    db: Session,
    *,
    ticket: Ticket,
    conversation: WebchatConversation,
    evidence_reference_id: int | None = None,
    now: datetime | None = None,
) -> ServerFactEvidence:
    """Resolve a short-lived, server-owned Tracking fact for one outbound reply.

    Client booleans are intentionally absent from this contract.  A fact can
    authorize a customer-visible statement only when the persisted Case Context
    is in the same tenant/case scope and the stored Tracking Truth metadata is
    current, primary, available, redacted and contradiction-free.
    """

    tenant_id = str(getattr(conversation, "tenant_key", None) or "default").strip() or "default"
    current = ensure_utc(now) or utc_now()

    query = db.query(CaseContextRecord).filter(
        CaseContextRecord.tenant_id == tenant_id,
        CaseContextRecord.ticket_id == ticket.id,
        CaseContextRecord.conversation_id == conversation.id,
    )
    if evidence_reference_id is not None:
        try:
            reference_id = int(evidence_reference_id)
        except (TypeError, ValueError):
            return _denied("invalid_evidence_reference")
        if reference_id <= 0:
            return _denied("invalid_evidence_reference")
        row = query.filter(CaseContextRecord.id == reference_id).first()
    else:
        row = (
            query.filter(
                CaseContextRecord.is_active.is_(True),
                CaseContextRecord.closed_at.is_(None),
            )
            .order_by(CaseContextRecord.id.desc())
            .first()
        )

    if row is None:
        return _denied("evidence_not_found_or_out_of_scope")
    fact = _fact_mapping(row)

    if not row.is_active or row.closed_at is not None or str(row.status or "").lower() in _TERMINAL_CONTEXT_STATUSES:
        return _denied("evidence_context_inactive", row, fact)
    expiry = ensure_utc(row.expires_at)
    if expiry is not None and expiry <= current:
        return _denied("evidence_context_expired", row, fact)
    if not fact:
        return _denied("evidence_payload_missing", row, fact)
    if fact.get("fact_evidence_present") is not True:
        return _denied("trusted_fact_not_present", row, fact)
    if fact.get("pii_redacted") is not True:
        return _denied("evidence_not_pii_redacted", row, fact)
    if str(fact.get("source_authority") or "") != SOURCE_AUTHORITY_PRIMARY:
        return _denied("evidence_not_primary_authority", row, fact)
    if str(fact.get("evidence_state") or "") != EVIDENCE_AVAILABLE:
        return _denied("evidence_state_not_available", row, fact)
    if str(fact.get("freshness") or "") != FRESHNESS_FRESH:
        return _denied("evidence_not_fresh", row, fact)
    if str(fact.get("tool_status") or "").lower() != "success":
        return _denied("evidence_tool_not_successful", row, fact)
    contradictions = fact.get("contradictions")
    if isinstance(contradictions, list) and contradictions:
        return _denied("evidence_has_contradictions", row, fact)
    if contradictions not in (None, [], ()):
        return _denied("evidence_contradictions_invalid", row, fact)

    checked_at = _parse_timestamp(fact.get("checked_at"))
    if checked_at is None:
        return _denied("evidence_checked_at_missing", row, fact)
    if checked_at > current + MAX_FUTURE_CLOCK_SKEW:
        return _denied("evidence_checked_at_in_future", row, fact)
    if current - checked_at > MAX_SERVER_FACT_AGE:
        return _denied("evidence_checked_at_too_old", row, fact)

    evidence_hash = _bounded_label(fact.get("tracking_number_hash"), limit=96) or _bounded_label(row.tracking_number_hash, limit=96)
    row_hash = _bounded_label(row.tracking_number_hash, limit=96)
    if row_hash and evidence_hash and row_hash != evidence_hash:
        return _denied("evidence_context_tracking_mismatch", row, fact)

    raw_tracking = str(getattr(ticket, "tracking_number", None) or getattr(conversation, "last_tracking_number", None) or "").strip()
    expected_hash = hash_tracking_number(raw_tracking) if raw_tracking else None
    if expected_hash and not evidence_hash:
        return _denied("evidence_tracking_binding_missing", row, fact)
    if expected_hash and evidence_hash != expected_hash:
        return _denied("evidence_tracking_binding_mismatch", row, fact)

    return ServerFactEvidence(
        present=True,
        reason="trusted_server_fact_available",
        reference_id=row.id,
        source=_bounded_label(fact.get("fact_source") or fact.get("source")),
        tool_name=_bounded_label(fact.get("tool_name")),
        authority=SOURCE_AUTHORITY_PRIMARY,
        evidence_state=EVIDENCE_AVAILABLE,
        freshness=FRESHNESS_FRESH,
        checked_at=_bounded_label(fact.get("checked_at"), limit=64),
        observed_at=_bounded_label(fact.get("observed_at"), limit=64),
        tracking_number_hash=evidence_hash,
        safe_tracking_reference=_bounded_label(fact.get("safe_tracking_reference"), limit=80) or _bounded_label(row.safe_tracking_reference, limit=80),
    )
