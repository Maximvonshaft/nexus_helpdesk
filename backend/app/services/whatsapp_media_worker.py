from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..models_whatsapp import WhatsAppMediaAsset
from ..utils.time import utc_now
from .whatsapp_media_processing_scope import (
    enforce_whatsapp_media_processing_scope,
)
from .whatsapp_media_service import (
    WhatsAppMediaError,
    download_and_persist_meta_media,
)


_MEDIA_LOCK_SECONDS = 300
_MEDIA_BATCH_LIMIT = 10


def dispatch_pending_whatsapp_media(
    db: Session,
    *,
    worker_id: str,
    limit: int = _MEDIA_BATCH_LIMIT,
) -> list[int]:
    """Claim and process Meta media inside the existing background Worker.

    WhatsAppMediaAsset remains the only media state authority. The lease fields
    prevent concurrent workers from downloading or projecting the same asset.
    """

    bounded_limit = max(1, min(int(limit), 50))
    lease_owner = (worker_id or "background-worker")[:120]
    claimed_ids = _claim_meta_media(
        db,
        worker_id=lease_owner,
        limit=bounded_limit,
    )
    processed: list[int] = []
    for asset_id in claimed_ids:
        try:
            asset = _load_claimed_asset(
                db,
                asset_id=asset_id,
                worker_id=lease_owner,
            )
            enforce_whatsapp_media_processing_scope(db, asset)
            download_and_persist_meta_media(db, asset=asset)
            asset.locked_at = None
            asset.locked_by = None
            asset.next_retry_at = None
            asset.updated_at = utc_now()
            db.commit()
            processed.append(asset_id)
        except WhatsAppMediaError as exc:
            db.rollback()
            _record_media_failure(
                db,
                asset_id=asset_id,
                worker_id=lease_owner,
                error_code=exc.code,
                retryable=exc.retryable,
            )
            processed.append(asset_id)
        except Exception as exc:
            db.rollback()
            _record_media_failure(
                db,
                asset_id=asset_id,
                worker_id=lease_owner,
                error_code=f"whatsapp_media_worker_{type(exc).__name__}",
                retryable=True,
            )
            processed.append(asset_id)
    return processed


def _claim_meta_media(
    db: Session,
    *,
    worker_id: str,
    limit: int,
) -> list[int]:
    now = utc_now()
    stale_before = now - timedelta(seconds=_MEDIA_LOCK_SECONDS)
    due = or_(
        WhatsAppMediaAsset.next_retry_at.is_(None),
        WhatsAppMediaAsset.next_retry_at <= now,
    )
    claimable = and_(
        WhatsAppMediaAsset.provider == "meta",
        WhatsAppMediaAsset.attempt_count < WhatsAppMediaAsset.max_attempts,
        or_(
            and_(
                WhatsAppMediaAsset.storage_status == "pending",
                due,
                WhatsAppMediaAsset.locked_at.is_(None),
            ),
            and_(
                WhatsAppMediaAsset.storage_status == "downloading",
                WhatsAppMediaAsset.locked_at < stale_before,
            ),
        ),
    )
    statement = (
        select(WhatsAppMediaAsset.id)
        .where(claimable)
        .order_by(
            WhatsAppMediaAsset.next_retry_at.asc().nullsfirst(),
            WhatsAppMediaAsset.created_at.asc(),
            WhatsAppMediaAsset.id.asc(),
        )
        .limit(limit)
    )
    bind = db.get_bind()
    if bind.dialect.name.startswith("postgresql"):
        statement = statement.with_for_update(skip_locked=True)
    ids = [int(row[0]) for row in db.execute(statement).all()]
    if not ids:
        db.rollback()
        return []
    result = db.execute(
        update(WhatsAppMediaAsset)
        .where(
            WhatsAppMediaAsset.id.in_(ids),
            claimable,
        )
        .values(
            storage_status="downloading",
            locked_at=now,
            locked_by=worker_id,
            updated_at=now,
        )
    )
    if result.rowcount != len(ids):
        db.rollback()
        return []
    db.commit()
    return ids


def _load_claimed_asset(
    db: Session,
    *,
    asset_id: int,
    worker_id: str,
) -> WhatsAppMediaAsset:
    row = (
        db.query(WhatsAppMediaAsset)
        .filter(
            WhatsAppMediaAsset.id == asset_id,
            WhatsAppMediaAsset.provider == "meta",
            WhatsAppMediaAsset.storage_status == "downloading",
            WhatsAppMediaAsset.locked_by == worker_id,
        )
        .first()
    )
    if row is None:
        raise WhatsAppMediaError("whatsapp_media_lease_lost", retryable=True)
    return row


def _record_media_failure(
    db: Session,
    *,
    asset_id: int,
    worker_id: str,
    error_code: str,
    retryable: bool,
) -> None:
    asset = db.get(WhatsAppMediaAsset, asset_id)
    if asset is None:
        db.rollback()
        return
    if asset.locked_by not in {None, worker_id}:
        db.rollback()
        return
    asset.attempt_count += 1
    asset.last_error_code = error_code[:120]
    asset.last_error_message = error_code[:500]
    asset.locked_at = None
    asset.locked_by = None
    terminal = (
        not retryable
        or asset.attempt_count >= asset.max_attempts
        or asset.storage_status in {"quarantined", "rejected", "deleted"}
    )
    if terminal:
        if asset.storage_status not in {"quarantined", "rejected", "deleted"}:
            asset.storage_status = "failed"
        asset.next_retry_at = None
    else:
        delay_seconds = min(30 * 2 ** max(asset.attempt_count - 1, 0), 1800)
        asset.storage_status = "pending"
        asset.next_retry_at = utc_now() + timedelta(seconds=delay_seconds)
    asset.updated_at = utc_now()
    db.commit()
