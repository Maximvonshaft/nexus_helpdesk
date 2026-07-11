from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_worker.py"


def _load_worker_module():
    spec = importlib.util.spec_from_file_location("nexus_run_worker_heartbeat_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_queue_names_map_to_stable_service_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _load_worker_module()
    calls: list[dict] = []
    sessions: list[FakeSession] = []

    def session_factory():
        session = FakeSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "update_service_heartbeat", lambda _db, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(worker.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_heartbeat_interval_seconds=30))
    worker._LAST_HEARTBEAT_AT.clear()

    for queue, expected in worker.WORKER_SERVICE_NAMES.items():
        worker._record_worker_heartbeat(
            worker_id=f"worker-{queue}",
            queue=queue,
            status="ok",
            processed=2,
            force=True,
        )
        assert calls[-1]["service_name"] == expected
        assert calls[-1]["status"] == "ok"
        assert calls[-1]["details"]["queue"] == queue
        assert calls[-1]["details"]["processed"] == 2

    assert all(session.commits == 1 and session.closed for session in sessions)
    assert "operations_dispatch_worker" not in worker.WORKER_SERVICE_NAMES.values()


def test_success_heartbeat_is_throttled_but_error_forces_write(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _load_worker_module()
    calls: list[dict] = []
    monotonic = iter((100.0, 105.0, 106.0))

    monkeypatch.setattr(worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker, "update_service_heartbeat", lambda _db, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_heartbeat_interval_seconds=30))
    worker._LAST_HEARTBEAT_AT.clear()

    worker._record_worker_heartbeat(worker_id="worker-background", queue="background", status="ok", processed=0)
    worker._record_worker_heartbeat(worker_id="worker-background", queue="background", status="ok", processed=0)
    worker._record_worker_heartbeat(worker_id="worker-background", queue="background", status="error", processed=0, force=True)

    assert len(calls) == 2
    assert calls[0]["status"] == "ok"
    assert calls[1]["status"] == "error"


def test_heartbeat_storage_failure_never_masks_worker_result(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _load_worker_module()
    session = FakeSession()

    monkeypatch.setattr(worker, "SessionLocal", lambda: session)
    monkeypatch.setattr(worker, "update_service_heartbeat", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    monkeypatch.setattr(worker.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_heartbeat_interval_seconds=30))
    worker._LAST_HEARTBEAT_AT.clear()

    worker._record_worker_heartbeat(
        worker_id="worker-webchat-ai",
        queue="webchat-ai",
        status="ok",
        processed=1,
        force=True,
    )

    assert session.rollbacks == 1
    assert session.closed is True
