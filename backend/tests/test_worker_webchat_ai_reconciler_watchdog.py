from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_run_worker_module():
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))
    module_path = backend_root / "scripts" / "run_worker.py"
    spec = importlib.util.spec_from_file_location("run_worker_for_watchdog_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeDB:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_webchat_ai_reconciler_watchdog_success_commits_and_closes(monkeypatch):
    run_worker = _load_run_worker_module()
    fake_db = FakeDB()

    monkeypatch.setattr(run_worker, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        run_worker,
        "reconcile_webchat_ai_state",
        lambda db: {
            "inspected": 1,
            "cleared": 0,
            "failed": 0,
            "promoted": 0,
            "timed_out": 1,
        },
    )

    run_worker._run_webchat_ai_reconciler_watchdog("worker-main")

    assert fake_db.committed is True
    assert fake_db.rolled_back is False
    assert fake_db.closed is True


def test_webchat_ai_reconciler_watchdog_exception_rolls_back_and_does_not_raise(monkeypatch):
    run_worker = _load_run_worker_module()
    fake_db = FakeDB()

    def boom(db):
        raise RuntimeError("synthetic watchdog failure")

    monkeypatch.setattr(run_worker, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(run_worker, "reconcile_webchat_ai_state", boom)

    run_worker._run_webchat_ai_reconciler_watchdog("worker-main")

    assert fake_db.committed is False
    assert fake_db.rolled_back is True
    assert fake_db.closed is True


def test_webchat_ai_reconciler_runs_from_dedicated_queue_when_enabled(monkeypatch):
    run_worker = _load_run_worker_module()
    calls = []

    class FakeContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(run_worker.settings, "webchat_ai_reconciler_enabled", True, raising=False)
    monkeypatch.setattr(run_worker, "db_context", lambda: FakeContext())
    monkeypatch.setattr(run_worker, "dispatch_pending_webchat_ai_reply_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(run_worker, "record_worker_poll", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, "record_worker_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, "_webchat_ai_reconciler_interval_seconds", lambda: 5)
    monkeypatch.setattr(run_worker, "_run_webchat_ai_reconciler_watchdog", lambda worker_id: calls.append(worker_id) or 0)
    monkeypatch.setattr(run_worker.time, "monotonic", lambda: 100.0)
    run_worker._LAST_WEBCHAT_AI_RECONCILER_RUN_AT = 0.0

    assert run_worker.run_queue_once("worker-webchat-ai-controlled", "webchat-ai") == 0
    assert calls == ["worker-webchat-ai-controlled"]


def test_webchat_ai_reconciler_can_be_disabled(monkeypatch):
    run_worker = _load_run_worker_module()
    calls = []

    class FakeContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(run_worker.settings, "webchat_ai_reconciler_enabled", False, raising=False)
    monkeypatch.setattr(run_worker, "db_context", lambda: FakeContext())
    monkeypatch.setattr(run_worker, "dispatch_pending_webchat_ai_reply_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(run_worker, "record_worker_result", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(run_worker, "_run_webchat_ai_reconciler_watchdog", lambda worker_id: (_ for _ in ()).throw(AssertionError("watchdog must stay disabled")))

    assert run_worker._run_webchat_ai("worker-webchat-ai-controlled") == 0
    assert calls


def test_webchat_ai_reconciler_interval_is_clamped(monkeypatch):
    run_worker = _load_run_worker_module()

    monkeypatch.setattr(run_worker.settings, "webchat_ai_reconciler_interval_seconds", 1, raising=False)

    assert run_worker._webchat_ai_reconciler_interval_seconds() == 5


def test_run_worker_main_uses_parsed_worker_id_and_queue(monkeypatch):
    run_worker = _load_run_worker_module()
    calls = []

    class Args:
        worker_id = "worker-webchat-ai-controlled"
        queue = "webchat-ai"
        once = True

    monkeypatch.setattr(run_worker.argparse.ArgumentParser, "parse_args", lambda self: Args())
    monkeypatch.setattr(run_worker, "run_queue_once", lambda worker_id, queue: calls.append((worker_id, queue)) or 0)

    assert run_worker.main() == 0
    assert calls == [("worker-webchat-ai-controlled", "webchat-ai")]
