from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..enums import EventType, JobStatus
from ..models import BackgroundJob, SpeedafAddressUpdateIdempotency, TicketEvent
from ..settings import get_settings
from ..utils.time import utc_now
from .speedaf.redactor import (
    mask_phone,
    safe_caller_payload,
    safe_waybill_payload,
    sha256_prefix,
    suffix,
)

settings = get_settings()
WEBCHAT_AI_REPLY_JOB = "webchat.ai_reply"
WEBCHAT_HANDOFF_SNAPSHOT_JOB = "webchat.handoff_snapshot"
SPEEDAF_WORK_ORDER_CREATE_JOB = "speedaf.work_order.create"
SPEEDAF_ADDRESS_UPDATE_JOB = "speedaf.address_update.submit"
SPEEDAF_VOICE_CALLBACK_JOB = "speedaf.voice.callback"
EMAIL_MAILBOX_SYNC_JOB = "email.mailbox_sync"
SPEEDAF_WORK_ORDER_DESCRIPTION_MAX_LENGTH = 200
SPEEDAF_SENSITIVE_JOB_TYPES = {
    SPEEDAF_WORK_ORDER_CREATE_JOB,
    SPEEDAF_ADDRESS_UPDATE_JOB,
    SPEEDAF_VOICE_CALLBACK_JOB,
}


def _stable_hash_prefix(value: object, *, length: int = 16) -> str:
    cleaned = str(value or "").strip().upper()
    return hashlib.sha256(
        cleaned.encode("utf-8", errors="ignore")
    ).hexdigest()[:length]


def _scrub_completed_speedaf_job_payload(job: BackgroundJob) -> None:
    if job.job_type not in SPEEDAF_SENSITIVE_JOB_TYPES:
        return
    try:
        payload = json.loads(job.payload_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    safe_payload: dict[str, object] = {
        "scrubbed": True,
        "scrub_reason": "speedaf_job_completed",
        "job_type": job.job_type,
    }
    for key in ("ticket_id", "conversation_id", "request_id"):
        if key in payload:
            safe_payload[key] = payload.get(key)
    if job.job_type == SPEEDAF_WORK_ORDER_CREATE_JOB:
        safe_payload.update(
            {
                "workOrderType": payload.get("workOrderType"),
                "description_present": bool(payload.get("description")),
                **safe_waybill_payload(str(payload.get("waybillCode") or "")),
                **safe_caller_payload(str(payload.get("callerID") or "")),
            }
        )
    elif job.job_type == SPEEDAF_ADDRESS_UPDATE_JOB:
        phone = str(payload.get("whatsAppPhone") or "")
        safe_payload.update(
            {
                "addressUpdateDedupeKey": payload.get("addressUpdateDedupeKey"),
                **safe_waybill_payload(str(payload.get("waybillCode") or "")),
                **safe_caller_payload(str(payload.get("callerID") or "")),
                "whatsapp_phone": {
                    "redacted": True,
                    "masked": mask_phone(phone),
                    "sha256_prefix": sha256_prefix(phone),
                },
            }
        )
    else:
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        safe_payload.update(
            {
                "voice_session_id": payload.get("voice_session_id"),
                "voiceCallbackDedupeKey": payload.get("voiceCallbackDedupeKey"),
                "call_session": {
                    "redacted": True,
                    "suffix": suffix(payload.get("callSessionId")),
                    "sha256_prefix": sha256_prefix(payload.get("callSessionId")),
                },
                "isTransferredToHuman": payload.get("isTransferredToHuman"),
                "action": {
                    "action": action.get("action"),
                    "actionStatus": action.get("actionStatus"),
                    "actionTime_present": bool(action.get("actionTime")),
                    "aiActionSummary_present": bool(action.get("aiActionSummary")),
                    "errorCode_present": bool(action.get("errorCode")),
                    **safe_waybill_payload(str(action.get("waybillCode") or "")),
                },
            }
        )
    job.payload_json = json.dumps(
        safe_payload,
        ensure_ascii=False,
        sort_keys=True,
    )


def _find_active_dedupe_job(
    db: Session,
    *,
    dedupe_key: str,
) -> BackgroundJob | None:
    query = db.query(BackgroundJob).filter(
        BackgroundJob.dedupe_key == dedupe_key,
        BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]),
    )
    if hasattr(query, "order_by"):
        query = query.order_by(BackgroundJob.id.desc())
    return query.first()


def _find_recent_dedupe_job(
    db: Session,
    *,
    dedupe_key: str,
    statuses: list[JobStatus],
    ttl: timedelta,
) -> BackgroundJob | None:
    cutoff = utc_now() - ttl
    return (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.dedupe_key == dedupe_key,
            BackgroundJob.status.in_(statuses),
            BackgroundJob.created_at >= cutoff,
        )
        .order_by(BackgroundJob.id.desc())
        .first()
    )


def find_recent_speedaf_voice_callback_job(
    db: Session,
    *,
    dedupe_key: str,
) -> BackgroundJob | None:
    return _find_recent_dedupe_job(
        db,
        dedupe_key=dedupe_key,
        statuses=[JobStatus.pending, JobStatus.processing, JobStatus.done],
        ttl=timedelta(hours=24),
    )


def enqueue_background_job(
    db: Session,
    *,
    queue_name: str,
    job_type: str,
    payload: dict,
    max_attempts: int | None = None,
    next_run_at=None,
    dedupe_key: str | None = None,
) -> BackgroundJob:
    if dedupe_key:
        existing = _find_active_dedupe_job(db, dedupe_key=dedupe_key)
        if existing is not None:
            return existing
    job = BackgroundJob(
        queue_name=queue_name,
        job_type=job_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
        dedupe_key=dedupe_key,
        status=JobStatus.pending,
        max_attempts=max_attempts or settings.job_max_retries,
        next_run_at=next_run_at,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        if dedupe_key:
            existing = _find_active_dedupe_job(db, dedupe_key=dedupe_key)
            if existing is not None:
                return existing
        raise
    return job


def enqueue_webchat_ai_reply_job(
    db: Session,
    *,
    conversation_id: int,
    ticket_id: int | None,
    visitor_message_id: int,
) -> BackgroundJob:
    return enqueue_background_job(
        db,
        queue_name="webchat_ai_reply",
        job_type=WEBCHAT_AI_REPLY_JOB,
        payload={
            "conversation_id": conversation_id,
            "ticket_id": ticket_id,
            "visitor_message_id": visitor_message_id,
        },
        dedupe_key=f"webchat-ai-reply:{visitor_message_id}",
    )


def enqueue_speedaf_work_order_create_job(
    db: Session,
    *,
    ticket_id: int,
    waybill_code: str,
    caller_id: str,
    description: str,
    work_order_type: str = "WT0103-05",
    conversation_id: int | None = None,
) -> BackgroundJob:
    payload = {
        "ticket_id": ticket_id,
        "conversation_id": conversation_id,
        "waybillCode": waybill_code,
        "callerID": caller_id,
        "workOrderType": work_order_type,
        "description": description[:SPEEDAF_WORK_ORDER_DESCRIPTION_MAX_LENGTH],
    }
    waybill_hash = _stable_hash_prefix(waybill_code)
    return enqueue_background_job(
        db,
        queue_name="speedaf_work_order",
        job_type=SPEEDAF_WORK_ORDER_CREATE_JOB,
        payload=payload,
        dedupe_key=(
            f"speedaf-workorder:ticket:{ticket_id}:"
            f"waybill:{waybill_hash}:type:{work_order_type}"
        ),
    )


def enqueue_speedaf_address_update_job(
    db: Session,
    *,
    ticket_id: int,
    waybill_code: str,
    caller_id: str,
    whatsapp_phone: str,
    dedupe_key: str,
    request_id: str | None = None,
) -> BackgroundJob:
    return enqueue_background_job(
        db,
        queue_name="speedaf_address_update",
        job_type=SPEEDAF_ADDRESS_UPDATE_JOB,
        payload={
            "ticket_id": ticket_id,
            "waybillCode": waybill_code,
            "callerID": caller_id,
            "whatsAppPhone": whatsapp_phone,
            "addressUpdateDedupeKey": dedupe_key,
            "request_id": request_id,
        },
        dedupe_key=dedupe_key,
    )


def enqueue_speedaf_voice_callback_job(
    db: Session,
    *,
    ticket_id: int,
    voice_session_id: int,
    call_session_id: str,
    is_transferred_to_human: bool,
    action: dict,
    dedupe_key: str,
    request_id: str | None = None,
) -> BackgroundJob:
    existing = find_recent_speedaf_voice_callback_job(
        db,
        dedupe_key=dedupe_key,
    )
    if existing is not None:
        return existing
    return enqueue_background_job(
        db,
        queue_name="speedaf_voice_callback",
        job_type=SPEEDAF_VOICE_CALLBACK_JOB,
        payload={
            "ticket_id": ticket_id,
            "voice_session_id": voice_session_id,
            "callSessionId": call_session_id,
            "isTransferredToHuman": 1 if is_transferred_to_human else 0,
            "action": action,
            "voiceCallbackDedupeKey": dedupe_key,
            "request_id": request_id,
        },
        dedupe_key=dedupe_key,
    )


def claim_pending_jobs(
    db: Session,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
    job_types: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[BackgroundJob]:
    worker_id = worker_id or f"job-worker-{uuid.uuid4().hex[:8]}"
    limit = limit or settings.job_batch_size
    now = utc_now()
    lock_deadline = now - timedelta(seconds=settings.job_lock_seconds)
    normalized_job_types = tuple(
        sorted({str(item) for item in (job_types or []) if item})
    )
    due_filter = or_(
        BackgroundJob.next_run_at.is_(None),
        BackgroundJob.next_run_at <= now,
    )
    stale_processing_filter = and_(
        BackgroundJob.status == JobStatus.processing,
        or_(
            BackgroundJob.locked_at.is_(None),
            BackgroundJob.locked_at < lock_deadline,
        ),
    )
    pending_filters = [
        or_(
            and_(BackgroundJob.status == JobStatus.pending, due_filter),
            stale_processing_filter,
        )
    ]
    if normalized_job_types:
        pending_filters.append(BackgroundJob.job_type.in_(normalized_job_types))
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        rows = db.execute(
            select(BackgroundJob.id)
            .where(*pending_filters)
            .order_by(BackgroundJob.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        claimed_ids = [row[0] for row in rows]
        if not claimed_ids:
            db.rollback()
            return []
        db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id.in_(claimed_ids))
            .values(
                status=JobStatus.processing,
                locked_at=now,
                locked_by=worker_id,
            )
        )
        db.commit()
    else:
        candidate_ids = [
            row[0]
            for row in (
                db.query(BackgroundJob.id)
                .filter(*pending_filters)
                .order_by(BackgroundJob.created_at.asc())
                .limit(limit)
                .all()
            )
        ]
        claimed_ids: list[int] = []
        for job_id in candidate_ids:
            claimed = db.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id, *pending_filters)
                .values(
                    status=JobStatus.processing,
                    locked_at=now,
                    locked_by=worker_id,
                )
            )
            if claimed.rowcount == 1:
                claimed_ids.append(job_id)
        if not claimed_ids:
            db.rollback()
            return []
        db.commit()
    return (
        db.query(BackgroundJob)
        .filter(BackgroundJob.id.in_(claimed_ids))
        .order_by(BackgroundJob.created_at.asc())
        .all()
    )


def _mark_done(job: BackgroundJob) -> None:
    _scrub_completed_speedaf_job_payload(job)
    job.status = JobStatus.done
    job.locked_at = None
    job.locked_by = None
    job.next_run_at = None
    job.last_error = None
    job.updated_at = utc_now()


def _mark_retry(job: BackgroundJob, reason: str) -> None:
    job.attempt_count += 1
    job.last_error = reason[:500]
    job.locked_at = None
    job.locked_by = None
    backoff_minutes = min(2 ** max(job.attempt_count - 1, 0), 30)
    if job.attempt_count >= job.max_attempts:
        job.status = JobStatus.dead
        job.next_run_at = None
    else:
        job.status = JobStatus.pending
        job.next_run_at = utc_now() + timedelta(minutes=backoff_minutes)
    job.updated_at = utc_now()


def _append_ticket_event(
    db: Session,
    *,
    ticket_id: int,
    note: str,
    payload: dict,
    field_name: str = "speedaf_work_order",
    new_value: str | None = None,
) -> None:
    db.add(
        TicketEvent(
            ticket_id=ticket_id,
            event_type=EventType.field_updated,
            field_name=field_name,
            new_value=new_value,
            note=note,
            payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            created_at=utc_now(),
        )
    )
    db.flush()


def _int_or_none(value) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _update_speedaf_address_idempotency_status(
    db: Session,
    *,
    dedupe_key: str,
    status_value: str,
) -> None:
    row = (
        db.query(SpeedafAddressUpdateIdempotency)
        .filter(SpeedafAddressUpdateIdempotency.dedupe_key == dedupe_key)
        .one_or_none()
    )
    if row is None:
        raise RuntimeError("speedaf_address_update_idempotency_reservation_missing")
    row.status = status_value
    row.updated_at = utc_now()
    db.flush()


def _process_speedaf_work_order_create_job(
    db: Session,
    job: BackgroundJob,
    payload: dict,
) -> None:
    from .speedaf.action_service import SpeedafActionDisabled, SpeedafActionService

    ticket_id = int(payload["ticket_id"])
    conversation_id = _int_or_none(payload.get("conversation_id"))
    result_payload: dict = {
        "job_id": job.id,
        "job_type": SPEEDAF_WORK_ORDER_CREATE_JOB,
        "ticket_id": ticket_id,
        "conversation_id": conversation_id,
        "workOrderType": payload.get("workOrderType") or "WT0103-05",
    }
    try:
        result = SpeedafActionService(
            ticket_id=ticket_id,
            webchat_conversation_id=conversation_id,
            background_job_id=job.id,
        ).create_work_order(
            waybill_code=str(payload["waybillCode"]),
            work_order_type=str(payload.get("workOrderType") or "WT0103-05"),
            description=str(payload.get("description") or "")[
                :SPEEDAF_WORK_ORDER_DESCRIPTION_MAX_LENGTH
            ],
            caller_id=str(payload["callerID"]),
        )
    except SpeedafActionDisabled as exc:
        result_payload.update(
            {
                "ok": False,
                "status": "disabled",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        _append_ticket_event(
            db,
            ticket_id=ticket_id,
            note="Speedaf work order creation skipped by feature gate.",
            payload=result_payload,
            new_value="skipped",
        )
        _mark_done(job)
        return
    result_payload.update(
        {
            "ok": result.ok,
            "status": result.status,
            "external_id": result.external_id,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "safe_payload": result.safe_payload,
        }
    )
    _append_ticket_event(
        db,
        ticket_id=ticket_id,
        note=(
            "Speedaf work order creation completed."
            if result.ok
            else "Speedaf work order creation failed."
        ),
        payload=result_payload,
        new_value="completed" if result.ok else "failed",
    )
    if not result.ok and result.retryable:
        raise RuntimeError(result.error_code or "speedaf_work_order_create_failed")


def _process_speedaf_address_update_job(
    db: Session,
    job: BackgroundJob,
    payload: dict,
) -> None:
    from .speedaf.action_service import SpeedafActionDisabled, SpeedafActionService

    ticket_id = int(payload["ticket_id"])
    dedupe_key = str(payload["addressUpdateDedupeKey"])
    phone = str(payload["whatsAppPhone"])
    result_payload: dict = {
        "job_id": job.id,
        "job_type": SPEEDAF_ADDRESS_UPDATE_JOB,
        "ticket_id": ticket_id,
        "dedupe_key": dedupe_key,
        **safe_waybill_payload(str(payload["waybillCode"])),
        "whatsapp_phone": {"redacted": True, "suffix": phone[-4:]},
    }
    try:
        result = SpeedafActionService(
            ticket_id=ticket_id,
            background_job_id=job.id,
            request_id=dedupe_key,
        ).submit_update_address_flow(
            waybill_code=str(payload["waybillCode"]),
            whatsapp_phone=phone,
            caller_id=str(payload["callerID"]),
        )
    except SpeedafActionDisabled as exc:
        _update_speedaf_address_idempotency_status(
            db,
            dedupe_key=dedupe_key,
            status_value="skipped",
        )
        result_payload.update(
            {
                "ok": False,
                "status": "disabled",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        _append_ticket_event(
            db,
            ticket_id=ticket_id,
            field_name="speedaf_address_update",
            new_value="skipped",
            note="Speedaf address update confirmation request skipped by feature gate.",
            payload=result_payload,
        )
        _mark_done(job)
        return
    result_payload.update(
        {
            "ok": result.ok,
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "safe_payload": result.safe_payload,
        }
    )
    status_value = "success" if result.ok else "failed"
    _update_speedaf_address_idempotency_status(
        db,
        dedupe_key=dedupe_key,
        status_value=status_value,
    )
    _append_ticket_event(
        db,
        ticket_id=ticket_id,
        field_name="speedaf_address_update",
        new_value="completed" if result.ok else "failed",
        note=(
            "Speedaf address update confirmation request completed. Final Speedaf confirmation may still be required."
            if result.ok
            else "Speedaf address update confirmation request failed."
        ),
        payload=result_payload,
    )
    if not result.ok and result.retryable:
        raise RuntimeError(result.error_code or "speedaf_address_update_failed")


def _process_speedaf_voice_callback_job(
    db: Session,
    job: BackgroundJob,
    payload: dict,
) -> None:
    from .speedaf.action_service import SpeedafActionDisabled, SpeedafActionService

    ticket_id = int(payload["ticket_id"])
    voice_session_id = _int_or_none(payload.get("voice_session_id"))
    dedupe_key = str(
        payload.get("voiceCallbackDedupeKey")
        or job.dedupe_key
        or f"speedaf-voice-callback:{job.id}"
    )
    action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
    result_payload: dict = {
        "job_id": job.id,
        "job_type": SPEEDAF_VOICE_CALLBACK_JOB,
        "ticket_id": ticket_id,
        "voice_session_id": voice_session_id,
        "dedupe_key": dedupe_key,
        **safe_waybill_payload(str(action.get("waybillCode") or "")),
    }
    callback_payload = {
        "callSessionId": str(payload.get("callSessionId") or ""),
        "isTransferredToHuman": int(payload.get("isTransferredToHuman") or 0),
        "action": action,
    }
    try:
        result = SpeedafActionService(
            ticket_id=ticket_id,
            background_job_id=job.id,
            request_id=dedupe_key,
        ).send_voice_callback(callback_payload)
    except SpeedafActionDisabled as exc:
        result_payload.update(
            {
                "ok": False,
                "status": "disabled",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        _append_ticket_event(
            db,
            ticket_id=ticket_id,
            field_name="speedaf_voice_callback",
            new_value="skipped",
            note="Speedaf voice callback skipped by feature gate.",
            payload=result_payload,
        )
        _mark_done(job)
        return
    result_payload.update(
        {
            "ok": result.ok,
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "safe_payload": result.safe_payload,
        }
    )
    _append_ticket_event(
        db,
        ticket_id=ticket_id,
        field_name="speedaf_voice_callback",
        new_value="completed" if result.ok else "failed",
        note=(
            "Speedaf voice callback completed."
            if result.ok
            else "Speedaf voice callback failed."
        ),
        payload=result_payload,
    )
    if not result.ok and result.retryable:
        raise RuntimeError(result.error_code or "speedaf_voice_callback_failed")


def process_background_job(
    db: Session,
    job: BackgroundJob,
) -> BackgroundJob:
    payload = json.loads(job.payload_json or "{}")
    try:
        if job.job_type == WEBCHAT_AI_REPLY_JOB:
            from .webchat_ai_orchestration_service import process_webchat_ai_reply_job

            raw_ticket_id = payload.get("ticket_id")
            process_webchat_ai_reply_job(
                db,
                conversation_id=int(payload["conversation_id"]),
                ticket_id=int(raw_ticket_id) if raw_ticket_id is not None else None,
                visitor_message_id=int(payload["visitor_message_id"]),
            )
        elif job.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB:
            from .webchat_handoff_snapshot_service import (
                process_webchat_handoff_snapshot_job,
            )

            snapshot = payload.get("snapshot")
            if not isinstance(snapshot, dict):
                raise RuntimeError("webchat handoff snapshot payload is required")
            process_webchat_handoff_snapshot_job(db, snapshot=snapshot)
        elif job.job_type == SPEEDAF_WORK_ORDER_CREATE_JOB:
            _process_speedaf_work_order_create_job(db, job, payload)
        elif job.job_type == SPEEDAF_ADDRESS_UPDATE_JOB:
            _process_speedaf_address_update_job(db, job, payload)
        elif job.job_type == SPEEDAF_VOICE_CALLBACK_JOB:
            _process_speedaf_voice_callback_job(db, job, payload)
        elif job.job_type == EMAIL_MAILBOX_SYNC_JOB:
            from .email_mailbox_polling_service import process_email_mailbox_sync_job

            process_email_mailbox_sync_job(
                db,
                account_id=int(payload["account_id"]),
            )
        else:
            raise RuntimeError(f"Unsupported job type: {job.job_type}")
        _mark_done(job)
        return job
    except Exception as exc:
        _mark_retry(job, str(exc))
        return job


def dispatch_pending_background_jobs(
    db: Session,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[BackgroundJob]:
    from .background_job_transaction_boundary import (
        dispatch_pending_background_jobs as canonical_dispatch,
    )

    return canonical_dispatch(db, limit=limit, worker_id=worker_id)


def dispatch_pending_webchat_ai_reply_jobs(
    db: Session,
    *,
    limit: int | None = None,
    worker_id: str | None = None,
) -> list[BackgroundJob]:
    from .background_job_transaction_boundary import (
        dispatch_pending_webchat_ai_reply_jobs as canonical_dispatch,
    )

    return canonical_dispatch(db, limit=limit, worker_id=worker_id)
