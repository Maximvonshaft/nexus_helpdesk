from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from ...models_operations_dispatch import OperationsDispatchOutboxRecord
from ...utils.time import ensure_utc, utc_now

_SAFE_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SAFE_CATEGORY_RE = re.compile(r"[^a-z0-9_.:-]+")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_TRACKING_RE = re.compile(
    r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b",
    re.I,
)
_SECRET_RE = re.compile(
    r"(?:\bbearer\s+\S+|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"\b(?:password|secret|api[_-]?key|token)\s*[:=]\s*\S+)",
    re.I,
)
_GROUP_ID_RE = re.compile(r"\b\d{10,24}@g\.us\b", re.I)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'-]{2,80}\s(?:street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|ulica|put|strasse|straße)\b",
    re.I,
)


class OperationsDispatchStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    RETRYABLE = "retryable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class OperationsDispatchEnqueueResult:
    record: OperationsDispatchOutboxRecord
    created: bool


class OperationsDispatchCollisionError(RuntimeError):
    """A dispatch_key exists under a different immutable scope."""


class OperationsDispatchLeaseLostError(RuntimeError):
    """The caller no longer owns a valid processing lease."""


def build_operations_dispatch_key(
    *,
    tenant_key: str,
    country_code: str,
    channel_key: str,
    routing_rule_id: int,
    ticket_id: int | None,
    case_reference: str | None = None,
) -> str:
    material = {
        "tenant_key": _scope(tenant_key, field="tenant_key", limit=80),
        "country_code": _scope(country_code, field="country_code", limit=16).upper(),
        "channel_key": _scope(channel_key, field="channel_key", limit=40).lower(),
        "routing_rule_id": _positive_int(routing_rule_id, field="routing_rule_id"),
        "ticket_id": _optional_positive_int(ticket_id, field="ticket_id"),
        "case_reference_hash": digest_identifier(case_reference) if case_reference else None,
    }
    return "ops-dispatch:" + hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def digest_identifier(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_hash_material(value)).hexdigest()


def enqueue_operations_dispatch(
    db: Session,
    *,
    dispatch_key: str,
    tenant_key: str,
    country_code: str,
    channel_key: str,
    routing_rule_id: int,
    destination_group_key: str,
    destination_group_hash: str,
    ticket_id: int | None = None,
    max_attempts: int = 5,
    now: datetime | None = None,
) -> OperationsDispatchEnqueueResult:
    """Create one durable dispatch or return the existing exact-scope row."""

    current = ensure_utc(now) or utc_now()
    try:
        parsed_max_attempts = int(max_attempts)
    except (TypeError, ValueError) as exc:
        raise ValueError("operations_dispatch_invalid_max_attempts") from exc

    values = {
        "dispatch_key": _scope(dispatch_key, field="dispatch_key", limit=80),
        "tenant_key": _scope(tenant_key, field="tenant_key", limit=80),
        "country_code": _scope(country_code, field="country_code", limit=16).upper(),
        "channel_key": _scope(channel_key, field="channel_key", limit=40).lower(),
        "routing_rule_id": _positive_int(routing_rule_id, field="routing_rule_id"),
        "destination_group_key": _scope(destination_group_key, field="destination_group_key", limit=200),
        "destination_group_hash": _digest(destination_group_hash, field="destination_group_hash"),
        "ticket_id": _optional_positive_int(ticket_id, field="ticket_id"),
        "max_attempts": min(20, max(1, parsed_max_attempts)),
    }

    existing = db.query(OperationsDispatchOutboxRecord).filter(
        OperationsDispatchOutboxRecord.dispatch_key == values["dispatch_key"]
    ).one_or_none()
    if existing is not None:
        _assert_same_scope(existing, values)
        return OperationsDispatchEnqueueResult(record=existing, created=False)

    candidate = OperationsDispatchOutboxRecord(
        **values,
        status=OperationsDispatchStatus.PENDING.value,
        attempt_count=0,
        next_retry_at=None,
        lease_owner=None,
        lease_expires_at=None,
        created_at=current,
        updated_at=current,
    )
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return OperationsDispatchEnqueueResult(record=candidate, created=True)
    except IntegrityError as exc:
        if object_session(candidate) is db:
            db.expunge(candidate)
        db.expire_all()
        winner = db.query(OperationsDispatchOutboxRecord).filter(
            OperationsDispatchOutboxRecord.dispatch_key == values["dispatch_key"]
        ).one_or_none()
        if winner is None:
            raise exc
        _assert_same_scope(winner, values)
        return OperationsDispatchEnqueueResult(record=winner, created=False)


def claim_next_operations_dispatch(
    db: Session,
    *,
    lease_owner: str,
    now: datetime | None = None,
    lease_seconds: int = 120,
) -> OperationsDispatchOutboxRecord | None:
    """Claim one due row in a short transaction.

    The caller must commit immediately after this function returns and before any
    provider call. PostgreSQL uses ``FOR UPDATE SKIP LOCKED``. Other databases
    use a compare-and-swap fallback for deterministic tests.
    """

    owner = _scope(lease_owner, field="lease_owner", limit=120)
    current = ensure_utc(now) or utc_now()
    lease_until = current + timedelta(seconds=max(1, int(lease_seconds)))
    _recover_expired_leases(db, current=current)
    _dead_letter_exhausted_due_rows(db, current=current)

    due_filter = or_(
        OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PENDING.value,
        and_(
            OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.RETRYABLE.value,
            OperationsDispatchOutboxRecord.next_retry_at <= current,
        ),
    )
    query = (
        db.query(OperationsDispatchOutboxRecord)
        .filter(due_filter)
        .filter(OperationsDispatchOutboxRecord.attempt_count < OperationsDispatchOutboxRecord.max_attempts)
        .order_by(OperationsDispatchOutboxRecord.created_at.asc(), OperationsDispatchOutboxRecord.id.asc())
    )

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        candidate = query.with_for_update(skip_locked=True).first()
        if candidate is None:
            return None
        candidate.status = OperationsDispatchStatus.PROCESSING.value
        candidate.attempt_count += 1
        candidate.lease_owner = owner
        candidate.lease_expires_at = lease_until
        candidate.next_retry_at = None
        candidate.error_category = None
        candidate.error_summary_redacted = None
        candidate.updated_at = current
        db.flush()
        return candidate

    candidate_ids = [row[0] for row in query.with_entities(OperationsDispatchOutboxRecord.id).limit(32).all()]
    for candidate_id in candidate_ids:
        claimed = db.execute(
            update(OperationsDispatchOutboxRecord)
            .where(OperationsDispatchOutboxRecord.id == candidate_id)
            .where(due_filter)
            .where(OperationsDispatchOutboxRecord.attempt_count < OperationsDispatchOutboxRecord.max_attempts)
            .values(
                status=OperationsDispatchStatus.PROCESSING.value,
                attempt_count=OperationsDispatchOutboxRecord.attempt_count + 1,
                lease_owner=owner,
                lease_expires_at=lease_until,
                next_retry_at=None,
                error_category=None,
                error_summary_redacted=None,
                updated_at=current,
            )
        )
        if claimed.rowcount == 1:
            db.expire_all()
            return db.get(OperationsDispatchOutboxRecord, candidate_id)
    return None


def mark_operations_dispatch_success(
    db: Session,
    *,
    record_id: int,
    lease_owner: str,
    provider_acknowledgement: Any = None,
    external_reference: Any = None,
    now: datetime | None = None,
) -> OperationsDispatchOutboxRecord:
    current = ensure_utc(now) or utc_now()
    record = _owned_processing_record(db, record_id=record_id, lease_owner=lease_owner, now=current)
    if record is None:
        raise OperationsDispatchLeaseLostError("operations_dispatch_lease_lost")
    record.status = OperationsDispatchStatus.DISPATCHED.value
    record.lease_owner = None
    record.lease_expires_at = None
    record.next_retry_at = None
    record.provider_acknowledgement = _bounded_marker(provider_acknowledgement, prefix="ack")
    record.external_reference_safe = _bounded_marker(external_reference, prefix="external")
    record.error_category = None
    record.error_summary_redacted = None
    record.dispatched_at = current
    record.updated_at = current
    db.flush()
    return record


def mark_operations_dispatch_failure(
    db: Session,
    *,
    record_id: int,
    lease_owner: str,
    error_category: Any,
    error_summary: Any = None,
    retryable: bool = True,
    now: datetime | None = None,
) -> OperationsDispatchOutboxRecord:
    current = ensure_utc(now) or utc_now()
    record = _owned_processing_record(db, record_id=record_id, lease_owner=lease_owner, now=current)
    if record is None:
        raise OperationsDispatchLeaseLostError("operations_dispatch_lease_lost")

    exhausted = record.attempt_count >= record.max_attempts
    if retryable and not exhausted:
        record.status = OperationsDispatchStatus.RETRYABLE.value
        record.next_retry_at = current + _retry_delay(record.attempt_count)
    elif exhausted:
        record.status = OperationsDispatchStatus.DEAD_LETTER.value
        record.next_retry_at = None
    else:
        record.status = OperationsDispatchStatus.FAILED.value
        record.next_retry_at = None

    record.lease_owner = None
    record.lease_expires_at = None
    record.error_category = _category(error_category)
    record.error_summary_redacted = _redact(error_summary, limit=320)
    record.updated_at = current
    db.flush()
    return record


def cancel_operations_dispatch(
    db: Session,
    *,
    record_id: int,
    reason: Any = None,
    now: datetime | None = None,
) -> OperationsDispatchOutboxRecord:
    current = ensure_utc(now) or utc_now()
    record = db.get(OperationsDispatchOutboxRecord, int(record_id))
    if record is None:
        raise LookupError("operations_dispatch_not_found")
    if record.status in {OperationsDispatchStatus.DISPATCHED.value, OperationsDispatchStatus.DEAD_LETTER.value}:
        raise RuntimeError("operations_dispatch_terminal")
    record.status = OperationsDispatchStatus.CANCELLED.value
    record.lease_owner = None
    record.lease_expires_at = None
    record.next_retry_at = None
    record.cancelled_at = current
    record.error_category = "cancelled"
    record.error_summary_redacted = _redact(reason, limit=320)
    record.updated_at = current
    db.flush()
    return record


def safe_operations_dispatch_reference(record: OperationsDispatchOutboxRecord) -> dict[str, Any]:
    return {
        "outbox_id": record.id,
        "dispatch_key": record.dispatch_key,
        "dispatch_status": record.status,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "tenant_key": record.tenant_key,
        "country_code": record.country_code,
        "channel_key": record.channel_key,
        "routing_rule_id": record.routing_rule_id,
        "destination_group_key": record.destination_group_key,
        "destination_group_hash": record.destination_group_hash,
        "error_category": record.error_category,
    }


def safe_operations_dispatch_error_category(value: Any) -> str | None:
    """Normalize legacy/read-path categories through the writer redaction boundary."""

    if value is None:
        return None
    return _category(value)


def _recover_expired_leases(db: Session, *, current: datetime) -> None:
    base = (
        OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PROCESSING.value,
        OperationsDispatchOutboxRecord.lease_expires_at <= current,
    )
    db.execute(
        update(OperationsDispatchOutboxRecord)
        .where(*base)
        .where(OperationsDispatchOutboxRecord.attempt_count >= OperationsDispatchOutboxRecord.max_attempts)
        .values(
            status=OperationsDispatchStatus.DEAD_LETTER.value,
            next_retry_at=None,
            lease_owner=None,
            lease_expires_at=None,
            error_category="lease_expired_attempts_exhausted",
            error_summary_redacted=None,
            updated_at=current,
        )
    )
    db.execute(
        update(OperationsDispatchOutboxRecord)
        .where(*base)
        .where(OperationsDispatchOutboxRecord.attempt_count < OperationsDispatchOutboxRecord.max_attempts)
        .values(
            status=OperationsDispatchStatus.RETRYABLE.value,
            next_retry_at=current,
            lease_owner=None,
            lease_expires_at=None,
            error_category="lease_expired",
            error_summary_redacted=None,
            updated_at=current,
        )
    )
    db.flush()


def _dead_letter_exhausted_due_rows(db: Session, *, current: datetime) -> None:
    db.execute(
        update(OperationsDispatchOutboxRecord)
        .where(OperationsDispatchOutboxRecord.status.in_([
            OperationsDispatchStatus.PENDING.value,
            OperationsDispatchStatus.RETRYABLE.value,
        ]))
        .where(OperationsDispatchOutboxRecord.attempt_count >= OperationsDispatchOutboxRecord.max_attempts)
        .values(
            status=OperationsDispatchStatus.DEAD_LETTER.value,
            next_retry_at=None,
            lease_owner=None,
            lease_expires_at=None,
            error_category="attempts_exhausted",
            error_summary_redacted=None,
            updated_at=current,
        )
    )
    db.flush()


def _owned_processing_record(
    db: Session,
    *,
    record_id: int,
    lease_owner: str,
    now: datetime,
) -> OperationsDispatchOutboxRecord | None:
    return (
        db.query(OperationsDispatchOutboxRecord)
        .filter(OperationsDispatchOutboxRecord.id == int(record_id))
        .filter(OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PROCESSING.value)
        .filter(OperationsDispatchOutboxRecord.lease_owner == _scope(lease_owner, field="lease_owner", limit=120))
        .filter(OperationsDispatchOutboxRecord.lease_expires_at > now)
        .with_for_update()
        .one_or_none()
    )


def _assert_same_scope(record: OperationsDispatchOutboxRecord, values: dict[str, Any]) -> None:
    fields = (
        "tenant_key",
        "country_code",
        "channel_key",
        "routing_rule_id",
        "destination_group_key",
        "destination_group_hash",
        "ticket_id",
    )
    if any(getattr(record, field) != values[field] for field in fields):
        raise OperationsDispatchCollisionError("operations_dispatch_key_scope_collision")


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(3600, max(30, 30 * (2 ** max(0, int(attempt_count) - 1))))
    return timedelta(seconds=seconds)


def _scope(value: Any, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or not _SAFE_SCOPE_RE.fullmatch(text):
        raise ValueError(f"operations_dispatch_invalid_{field}")
    return text


def _positive_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"operations_dispatch_invalid_{field}") from exc
    if parsed <= 0:
        raise ValueError(f"operations_dispatch_invalid_{field}")
    return parsed


def _optional_positive_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field=field)


def _digest(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", text):
        raise ValueError(f"operations_dispatch_invalid_{field}")
    return text


def _category(value: Any) -> str:
    raw = " ".join(str(value or "dispatch_failed").strip().split())
    if _contains_sensitive_value(raw):
        return "redacted_error_category"
    text = _SAFE_CATEGORY_RE.sub("_", raw.lower()).strip("_.:-")
    return (text or "dispatch_failed")[:80]


def _redact(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    text = _SECRET_RE.sub("[redacted_secret]", text)
    text = _GROUP_ID_RE.sub("[redacted_provider_group]", text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    text = _ADDRESS_RE.sub("[redacted_address]", text)
    text = _TRACKING_RE.sub("[redacted_tracking]", text)
    return text[:limit]


def _contains_sensitive_value(value: str) -> bool:
    return bool(
        _SECRET_RE.search(value)
        or _GROUP_ID_RE.search(value)
        or _EMAIL_RE.search(value)
        or _PHONE_RE.search(value)
        or _ADDRESS_RE.search(value)
        or _TRACKING_RE.search(value)
    )


def _bounded_marker(value: Any, *, prefix: str) -> str | None:
    if value is None or (isinstance(value, str) and not value):
        return None
    return f"{prefix}:sha256:{hashlib.sha256(_hash_material(value)).hexdigest()[:32]}"


def _hash_material(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8", errors="ignore")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: {"type": type(item).__name__[:64]},
        ).encode("utf-8", errors="ignore")
    except Exception:
        return type(value).__name__.encode("utf-8", errors="ignore")
