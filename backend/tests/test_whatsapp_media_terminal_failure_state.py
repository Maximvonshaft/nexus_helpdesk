from __future__ import annotations

from types import SimpleNamespace

from app.services.whatsapp_media_worker import _record_media_failure


class _FakeSession:
    def __init__(self, asset):
        self.asset = asset
        self.commits = 0
        self.rollbacks = 0

    def get(self, _model, _asset_id):
        return self.asset

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _claimed_asset():
    return SimpleNamespace(
        locked_by="media-worker-1",
        locked_at=object(),
        attempt_count=0,
        max_attempts=3,
        last_error_code=None,
        last_error_message=None,
        storage_status="downloading",
        scan_status="pending",
        next_retry_at=object(),
        updated_at=None,
    )


def test_malware_failure_restores_quarantine_and_infected_state() -> None:
    asset = _claimed_asset()
    db = _FakeSession(asset)

    _record_media_failure(
        db,
        asset_id=7,
        worker_id="media-worker-1",
        error_code="whatsapp_media_malware_detected",
        retryable=False,
        terminal_storage_status="quarantined",
        terminal_scan_status="infected",
    )

    assert asset.attempt_count == 1
    assert asset.storage_status == "quarantined"
    assert asset.scan_status == "infected"
    assert asset.last_error_code == "whatsapp_media_malware_detected"
    assert asset.next_retry_at is None
    assert asset.locked_by is None
    assert asset.locked_at is None
    assert db.commits == 1
    assert db.rollbacks == 0


def test_retryable_transport_failure_returns_asset_to_pending() -> None:
    asset = _claimed_asset()
    db = _FakeSession(asset)

    _record_media_failure(
        db,
        asset_id=8,
        worker_id="media-worker-1",
        error_code="meta_media_transport_error",
        retryable=True,
    )

    assert asset.storage_status == "pending"
    assert asset.scan_status == "pending"
    assert asset.next_retry_at is not None
    assert db.commits == 1
