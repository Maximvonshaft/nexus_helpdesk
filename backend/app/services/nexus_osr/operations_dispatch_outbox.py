from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models_operations_dispatch import OperationsDispatchOutboxRecord
from ...utils.time import utc_now


DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 60
DEFAULT_BACKOFF_BASE_SECONDS = 30
DEFAULT_BACKOFF_MAX_SECONDS = 30 * 60

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_TRACKING_RE = re.compile(
    r"\b(?=[A-Z0-9-]{8,35}\b)(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]*[A-Z0-9]\b",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(
    r"\b(?:address|addr|street|st\.|road|rd\.|avenue|ave\.|postcode|postal code|zip|地址)\b[:：]?\s+[^\n;|]{3,120}",
    re.IGNORECASE,
)
_PROVIDER_GROUP_RE = re.compile(
    r"\b(?:[A-Z0-9._-]+@g\.us|provider[-_ ]?[A-Z0-9_-]*group[A-Z0-9_-]*|group[-_ ]?id\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?:token|secret|password|credential|authorization|api[-_ ]?key)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:phone|email|tracking|address|provider.*group|group.*id|destination.*id|"
    r"token|secret|password|credential|authorization|raw_payload)",
    re.IGNORECASE,
)
_SAFE_SCOPE_KEY_RE = re.compile(r"[A-Za-z0-9_.:-]+")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_MAPPING_KEY_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_ACK_ALLOWED_KEYS = {
    "status",
    "code",
    "result",
    "accepted",
    "acknowledged",
    "duplicate",
    "retryable",
    "idempotent_replay",
}


class OperationsDispatchStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    RETRYABLE = "retryable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


TERMINAL_STATUSES = {
    OperationsDispatchStatus.DISPATCHED.value,
    OperationsDispatchStatus.FAILED.value,
    OperationsDispatchStatus.CANCELLED.value,
    OperationsDispatchStatus.DEAD_LETTER.value,
}


class DispatchLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnqueueOperationsDispatchResult:
    record: OperationsDispatchOutboxRecord
    created: bool


def sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def build_dispatch_key(*parts: Any) -> str:
    normalized = "|".join(str(part or "").strip() for part in parts)
    return sha256_digest(normalized)


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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> EnqueueOperationsDispatchResult:
    """Insert one durable dispatch record, resolving concurrent duplicates safely."""

    normalized_key = _required_digest(dispatch_key, "dispatch_key")
    existing = _find_by_dispatch_key(db, normalized_key)
    if existing is not None:
        return EnqueueOperationsDispatchResult(record=existing, created=False)

    row = OperationsDispatchOutboxRecord(
        ticket_id=ticket_id,
        dispatch_key=normalized_key,
        tenant_key=_required_scope_key(tenant_key or "default", "tenant_key", limit=80),
        country_code=_required_scope_key(country_code, "country_code", limit=16).upper(),
        channel_key=_required_scope_key(channel_key, "channel_key", limit=40).lower(),
        routing_rule_id=int(routing_rule_id),
        destination_group_key=_required_scope_key(destination_group_key, "destination_group_key", limit=200),
        destination_group_hash=_required_digest(destination_group_hash, "destination_group_hash"),
        status=OperationsDispatchStatus.PENDING.value,
        attempt_count=0,
        max_attempts=max(1, int(max_attempts)),
        next_retry_at=None,
        created_at=now or utc_now(),
        updated_at=now or utc_now(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return EnqueueOperationsDispatchResult(record=row, created=True)
    except IntegrityError:
        db.expire_all()
        existing = _find_by_dispatch_key(db, normalized_key)
        if existing is None:
            raise
        return EnqueueOperationsDispatchResult(record=existing, created=False)


def claim_next_operations_dispatch(
    db: Session,
    *,
    lease_owner: str,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    tenant_key: str | None = None,
    country_code: str | None = None,
    channel_key: str | None = None,
) -> OperationsDispatchOutboxRecord | None:
    """Acquire one eligible record using a compare-and-set lease.

    The conditional UPDATE is the safety boundary. A preliminary SELECT may race,
    but only one worker can change the same eligible row to `processing` with the
    observed status, attempt count and lease state.
    """

    current = now or utc_now()
    owner = _required(lease_owner, "lease_owner", limit=120)
    lease_until = current + timedelta(seconds=max(1, int(lease_seconds)))

    _dead_letter_exhausted(db, now=current)

    base_filters = [_eligible_filter(current)]
    if tenant_key is not None:
        base_filters.append(OperationsDispatchOutboxRecord.tenant_key == str(tenant_key))
    if country_code is not None:
        base_filters.append(OperationsDispatchOutboxRecord.country_code == str(country_code).upper())
    if channel_key is not None:
        base_filters.append(OperationsDispatchOutboxRecord.channel_key == str(channel_key).lower())

    candidates = (
        db.query(OperationsDispatchOutboxRecord)
        .filter(*base_filters)
        .filter(OperationsDispatchOutboxRecord.attempt_count < OperationsDispatchOutboxRecord.max_attempts)
        .order_by(
            OperationsDispatchOutboxRecord.next_retry_at.asc(),
            OperationsDispatchOutboxRecord.created_at.asc(),
            OperationsDispatchOutboxRecord.id.asc(),
        )
        .limit(20)
        .all()
    )

    for candidate in candidates:
        observed_status = candidate.status
        observed_attempts = candidate.attempt_count
        observed_lease_owner = candidate.lease_owner
        observed_lease_expires_at = candidate.lease_expires_at
        rowcount = (
            db.query(OperationsDispatchOutboxRecord)
            .filter(OperationsDispatchOutboxRecord.id == candidate.id)
            .filter(OperationsDispatchOutboxRecord.status == observed_status)
            .filter(OperationsDispatchOutboxRecord.attempt_count == observed_attempts)
            .filter(_same_nullable(OperationsDispatchOutboxRecord.lease_owner, observed_lease_owner))
            .filter(_same_nullable(OperationsDispatchOutboxRecord.lease_expires_at, observed_lease_expires_at))
            .filter(_eligible_filter(current))
            .filter(OperationsDispatchOutboxRecord.attempt_count < OperationsDispatchOutboxRecord.max_attempts)
            .update(
                {
                    OperationsDispatchOutboxRecord.status: OperationsDispatchStatus.PROCESSING.value,
                    OperationsDispatchOutboxRecord.attempt_count: observed_attempts + 1,
                    OperationsDispatchOutboxRecord.lease_owner: owner,
                    OperationsDispatchOutboxRecord.lease_expires_at: lease_until,
                    OperationsDispatchOutboxRecord.next_retry_at: None,
                    OperationsDispatchOutboxRecord.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if rowcount == 1:
            db.flush()
            db.expire_all()
            return db.get(OperationsDispatchOutboxRecord, candidate.id)
        db.expire_all()
    return None


def mark_operations_dispatch_succeeded(
    db: Session,
    *,
    record_id: int,
    lease_owner: str,
    provider_acknowledgement: Any = None,
    external_reference: str | None = None,
    now: datetime | None = None,
) -> OperationsDispatchOutboxRecord:
    current = now or utc_now()
    updates = {
        OperationsDispatchOutboxRecord.status: OperationsDispatchStatus.DISPATCHED.value,
        OperationsDispatchOutboxRecord.provider_acknowledgement: sanitize_provider_acknowledgement(provider_acknowledgement),
        OperationsDispatchOutboxRecord.external_reference_safe: safe_external_reference(external_reference),
        OperationsDispatchOutboxRecord.error_category: None,
        OperationsDispatchOutboxRecord.error_summary_redacted: None,
        OperationsDispatchOutboxRecord.next_retry_at: None,
        OperationsDispatchOutboxRecord.lease_owner: None,
        OperationsDispatchOutboxRecord.lease_expires_at: None,
        OperationsDispatchOutboxRecord.dispatched_at: current,
        OperationsDispatchOutboxRecord.updated_at: current,
    }
    return _transition_owned_processing(db, record_id=record_id, lease_owner=lease_owner, now=current, updates=updates)


def mark_operations_dispatch_failed(
    db: Session,
    *,
    record_id: int,
    lease_owner: str,
    retryable: bool,
    error_category: str | None,
    error_summary: Any = None,
    provider_acknowledgement: Any = None,
    now: datetime | None = None,
    backoff_base_seconds: int = DEFAULT_BACKOFF_BASE_SECONDS,
    backoff_max_seconds: int = DEFAULT_BACKOFF_MAX_SECONDS,
) -> OperationsDispatchOutboxRecord:
    current = now or utc_now()
    record = _owned_processing_record(db, record_id=record_id, lease_owner=lease_owner, now=current)
    if record is None:
        raise DispatchLeaseLostError(f"dispatch lease lost for record {record_id}")

    exhausted = record.attempt_count >= record.max_attempts
    if retryable and not exhausted:
        status = OperationsDispatchStatus.RETRYABLE.value
        delay = controlled_backoff_seconds(
            attempt_count=record.attempt_count,
            base_seconds=backoff_base_seconds,
            max_seconds=backoff_max_seconds,
        )
        next_retry_at = current + timedelta(seconds=delay)
    elif retryable:
        status = OperationsDispatchStatus.DEAD_LETTER.value
        next_retry_at = None
    else:
        status = OperationsDispatchStatus.FAILED.value
        next_retry_at = None

    updates = {
        OperationsDispatchOutboxRecord.status: status,
        OperationsDispatchOutboxRecord.provider_acknowledgement: sanitize_provider_acknowledgement(provider_acknowledgement),
        OperationsDispatchOutboxRecord.error_category: sanitize_error_category(error_category),
        OperationsDispatchOutboxRecord.error_summary_redacted: redact_dispatch_text(error_summary, limit=1000),
        OperationsDispatchOutboxRecord.next_retry_at: next_retry_at,
        OperationsDispatchOutboxRecord.lease_owner: None,
        OperationsDispatchOutboxRecord.lease_expires_at: None,
        OperationsDispatchOutboxRecord.updated_at: current,
    }
    return _transition_owned_processing(db, record_id=record_id, lease_owner=lease_owner, now=current, updates=updates)


def cancel_operations_dispatch(
    db: Session,
    *,
    record_id: int,
    reason: Any = None,
    now: datetime | None = None,
) -> OperationsDispatchOutboxRecord:
    current = now or utc_now()
    rowcount = (
        db.query(OperationsDispatchOutboxRecord)
        .filter(OperationsDispatchOutboxRecord.id == int(record_id))
        .filter(OperationsDispatchOutboxRecord.status.notin_(list(TERMINAL_STATUSES)))
        .update(
            {
                OperationsDispatchOutboxRecord.status: OperationsDispatchStatus.CANCELLED.value,
                OperationsDispatchOutboxRecord.error_category: "cancelled",
                OperationsDispatchOutboxRecord.error_summary_redacted: redact_dispatch_text(reason, limit=1000),
                OperationsDispatchOutboxRecord.next_retry_at: None,
                OperationsDispatchOutboxRecord.lease_owner: None,
                OperationsDispatchOutboxRecord.lease_expires_at: None,
                OperationsDispatchOutboxRecord.cancelled_at: current,
                OperationsDispatchOutboxRecord.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if rowcount != 1:
        record = db.get(OperationsDispatchOutboxRecord, int(record_id))
        if record is None:
            raise LookupError(f"dispatch record {record_id} not found")
        return record
    db.flush()
    db.expire_all()
    record = db.get(OperationsDispatchOutboxRecord, int(record_id))
    assert record is not None
    return record


def controlled_backoff_seconds(
    *,
    attempt_count: int,
    base_seconds: int = DEFAULT_BACKOFF_BASE_SECONDS,
    max_seconds: int = DEFAULT_BACKOFF_MAX_SECONDS,
) -> int:
    base = max(1, int(base_seconds))
    cap = max(base, int(max_seconds))
    exponent = max(0, int(attempt_count) - 1)
    return min(cap, base * (2**exponent))


def audit_reference_payload(record: OperationsDispatchOutboxRecord, *, event: str) -> dict[str, Any]:
    """Return a timeline-safe pointer; never include message/customer/provider payloads."""

    return {
        "event": str(event),
        "source": "nexus_osr",
        "outbox_id": record.id,
        "dispatch_key": record.dispatch_key,
        "dispatch_status": record.status,
        "tenant_key": record.tenant_key,
        "country_code": record.country_code,
        "channel_key": record.channel_key,
        "routing_rule_id": record.routing_rule_id,
        "destination_group_key": record.destination_group_key,
        "destination_group_hash": record.destination_group_hash,
        "attempt_count": record.attempt_count,
    }


def sanitize_provider_acknowledgement(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        omitted_fields = 0
        for key, item in value.items():
            key_text = str(key).strip().lower()
            if key_text in _ACK_ALLOWED_KEYS:
                safe[key_text] = _sanitize_value(item, limit=300)
            else:
                omitted_fields += 1
        if omitted_fields:
            safe["omitted_fields"] = omitted_fields
        return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:1000]
    if isinstance(value, (list, tuple)):
        return json.dumps(
            [_sanitize_value(item, limit=200) for item in value[:20]],
            ensure_ascii=False,
            separators=(",", ":"),
        )[:1000]
    return redact_dispatch_text(value, limit=1000)


def safe_external_reference(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return sha256_digest(cleaned)


def sanitize_error_category(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if (
        _PROVIDER_GROUP_RE.search(raw)
        or _EMAIL_RE.search(raw)
        or _PHONE_RE.search(raw)
        or _TRACKING_RE.search(raw)
        or _ADDRESS_RE.search(raw)
    ):
        return "redacted_error_category"
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", raw.lower()).strip("_")
    return cleaned[:80] or None


def redact_dispatch_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        value = _sanitize_value(value, limit=limit)
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = str(value)
    text = _PROVIDER_GROUP_RE.sub("[redacted_provider_group]", text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    text = _ADDRESS_RE.sub("[redacted_address]", text)
    text = _SECRET_ASSIGNMENT_RE.sub("[redacted_secret]", text)
    text = _TRACKING_RE.sub(_redact_tracking_match, text)
    return text[: max(1, int(limit))]


def _redact_tracking_match(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.lower().startswith("sha256"):
        return token
    return "[redacted_tracking]"


def _sanitize_value(value: Any, *, limit: int) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items(), start=1):
            key_text = str(key).strip()
            safe_key = key_text if _safe_mapping_key(key_text) else f"field_{index}"
            result[safe_key] = (
                "[redacted]" if _SENSITIVE_KEY_RE.search(key_text) else _sanitize_value(item, limit=limit)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, limit=limit) for item in value[:50]]
    if isinstance(value, str):
        return redact_dispatch_text(value, limit=limit)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact_dispatch_text(str(value), limit=limit)


def _eligible_filter(now: datetime):
    due = or_(
        OperationsDispatchOutboxRecord.next_retry_at.is_(None),
        OperationsDispatchOutboxRecord.next_retry_at <= now,
    )
    lease_expired = and_(
        OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PROCESSING.value,
        OperationsDispatchOutboxRecord.lease_expires_at.is_not(None),
        OperationsDispatchOutboxRecord.lease_expires_at <= now,
    )
    return or_(
        and_(OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PENDING.value, due),
        and_(OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.RETRYABLE.value, due),
        lease_expired,
    )


def _dead_letter_exhausted(db: Session, *, now: datetime) -> None:
    retry_exhausted = and_(
        OperationsDispatchOutboxRecord.status.in_(
            [OperationsDispatchStatus.PENDING.value, OperationsDispatchStatus.RETRYABLE.value]
        ),
        OperationsDispatchOutboxRecord.attempt_count >= OperationsDispatchOutboxRecord.max_attempts,
    )
    crashed_exhausted = and_(
        OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PROCESSING.value,
        OperationsDispatchOutboxRecord.attempt_count >= OperationsDispatchOutboxRecord.max_attempts,
        OperationsDispatchOutboxRecord.lease_expires_at.is_not(None),
        OperationsDispatchOutboxRecord.lease_expires_at <= now,
    )
    db.query(OperationsDispatchOutboxRecord).filter(or_(retry_exhausted, crashed_exhausted)).update(
        {
            OperationsDispatchOutboxRecord.status: OperationsDispatchStatus.DEAD_LETTER.value,
            OperationsDispatchOutboxRecord.error_category: "max_attempts_exhausted",
            OperationsDispatchOutboxRecord.error_summary_redacted: "maximum dispatch attempts exhausted",
            OperationsDispatchOutboxRecord.next_retry_at: None,
            OperationsDispatchOutboxRecord.lease_owner: None,
            OperationsDispatchOutboxRecord.lease_expires_at: None,
            OperationsDispatchOutboxRecord.updated_at: now,
        },
        synchronize_session=False,
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
        .filter(OperationsDispatchOutboxRecord.lease_owner == str(lease_owner))
        .filter(OperationsDispatchOutboxRecord.lease_expires_at.is_not(None))
        .filter(OperationsDispatchOutboxRecord.lease_expires_at > now)
        .one_or_none()
    )


def _transition_owned_processing(
    db: Session,
    *,
    record_id: int,
    lease_owner: str,
    now: datetime,
    updates: dict[Any, Any],
) -> OperationsDispatchOutboxRecord:
    rowcount = (
        db.query(OperationsDispatchOutboxRecord)
        .filter(OperationsDispatchOutboxRecord.id == int(record_id))
        .filter(OperationsDispatchOutboxRecord.status == OperationsDispatchStatus.PROCESSING.value)
        .filter(OperationsDispatchOutboxRecord.lease_owner == str(lease_owner))
        .filter(OperationsDispatchOutboxRecord.lease_expires_at.is_not(None))
        .filter(OperationsDispatchOutboxRecord.lease_expires_at > now)
        .update(updates, synchronize_session=False)
    )
    if rowcount != 1:
        raise DispatchLeaseLostError(f"dispatch lease lost for record {record_id}")
    db.flush()
    db.expire_all()
    record = db.get(OperationsDispatchOutboxRecord, int(record_id))
    assert record is not None
    return record


def _find_by_dispatch_key(db: Session, dispatch_key: str) -> OperationsDispatchOutboxRecord | None:
    return (
        db.query(OperationsDispatchOutboxRecord)
        .filter(OperationsDispatchOutboxRecord.dispatch_key == dispatch_key)
        .one_or_none()
    )


def _required(value: Any, field_name: str, *, limit: int) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return cleaned


def _required_scope_key(value: Any, field_name: str, *, limit: int) -> str:
    cleaned = _required(value, field_name, limit=limit)
    if _SAFE_SCOPE_KEY_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{field_name} contains unsafe characters")
    return cleaned


def _required_digest(value: Any, field_name: str) -> str:
    cleaned = _required(value, field_name, limit=80)
    if _DIGEST_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{field_name} must be a sha256 digest")
    return cleaned


def _safe_mapping_key(value: str) -> bool:
    if _SAFE_MAPPING_KEY_RE.fullmatch(value) is None:
        return False
    return not (
        _SENSITIVE_KEY_RE.search(value)
        or _PROVIDER_GROUP_RE.search(value)
        or _EMAIL_RE.search(value)
        or _PHONE_RE.search(value)
        or _TRACKING_RE.fullmatch(value)
    )


def _same_nullable(column, value: Any):
    return column.is_(None) if value is None else column == value
