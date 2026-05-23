from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..utils.time import ensure_utc, format_utc, utc_now
from ..voice_models import WebchatVoiceSession

TERMINAL_STATUSES = {"ended", "missed", "rejected", "failed", "cancelled", "canceled", "expired"}
ACTIVE_STALE_STATUSES = {"accepted", "active"}


@dataclass(frozen=True)
class VoiceSessionReconcileItem:
    public_id: str
    previous_status: str
    target_status: str
    expires_at: str | None
    target_ended_at: str | None
    action: str


@dataclass
class VoiceSessionReconcileResult:
    ok: bool
    dry_run: bool
    limit: int
    older_than_seconds: int
    eligible_count: int
    processed_count: int
    updated_count: int
    skipped_count: int
    by_previous_status: dict[str, int]
    by_target_status: dict[str, int]
    items: list[VoiceSessionReconcileItem]
    warnings: list[str]

    def to_safe_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_now(value: datetime | None) -> datetime:
    return ensure_utc(value) or utc_now()


def _target_status(session: WebchatVoiceSession) -> str:
    status = (session.status or "").strip().lower()
    if session.accepted_at is not None or session.active_at is not None or status in ACTIVE_STALE_STATUSES:
        return "ended"
    return "missed"


def _lock_if_supported(query):
    bind = query.session.get_bind()
    if bind is not None and bind.dialect.name.startswith("postgresql"):
        return query.with_for_update(skip_locked=True)
    return query


def reconcile_stale_webchat_voice_sessions(
    db: Session,
    *,
    now: datetime | None = None,
    dry_run: bool = True,
    limit: int = 100,
    older_than_seconds: int = 0,
    worker_id: str = "manual-stale-voice-reconciler",
) -> VoiceSessionReconcileResult:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds must be greater than or equal to 0")

    current = _ensure_now(now)
    cutoff = current - timedelta(seconds=older_than_seconds)

    base_query = db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.expires_at.isnot(None),
        WebchatVoiceSession.expires_at < cutoff,
        WebchatVoiceSession.ended_at.is_(None),
        ~WebchatVoiceSession.status.in_(sorted(TERMINAL_STATUSES)),
    )
    eligible_count = base_query.count()
    selected_query = _lock_if_supported(
        base_query.order_by(WebchatVoiceSession.expires_at.asc(), WebchatVoiceSession.id.asc()).limit(limit)
    )
    sessions = selected_query.all()

    items: list[VoiceSessionReconcileItem] = []
    by_previous_status: dict[str, int] = {}
    by_target_status: dict[str, int] = {}
    updated_count = 0

    for session in sessions:
        previous_status = (session.status or "").strip().lower()
        target_status = _target_status(session)
        target_ended_at = ensure_utc(session.expires_at) or current
        by_previous_status[previous_status] = by_previous_status.get(previous_status, 0) + 1
        by_target_status[target_status] = by_target_status.get(target_status, 0) + 1

        if not dry_run:
            session.status = target_status
            session.ended_at = target_ended_at
            session.updated_at = current
            updated_count += 1

        items.append(
            VoiceSessionReconcileItem(
                public_id=session.public_id,
                previous_status=previous_status,
                target_status=target_status,
                expires_at=format_utc(session.expires_at),
                target_ended_at=format_utc(target_ended_at),
                action="would_update" if dry_run else "updated",
            )
        )

    warnings: list[str] = []
    if eligible_count > len(sessions):
        warnings.append("result limited; rerun with another batch to process remaining stale voice sessions")
    if not dry_run:
        db.flush()

    return VoiceSessionReconcileResult(
        ok=True,
        dry_run=dry_run,
        limit=limit,
        older_than_seconds=older_than_seconds,
        eligible_count=eligible_count,
        processed_count=len(sessions),
        updated_count=updated_count,
        skipped_count=max(eligible_count - len(sessions), 0),
        by_previous_status=by_previous_status,
        by_target_status=by_target_status,
        items=items,
        warnings=warnings,
    )
