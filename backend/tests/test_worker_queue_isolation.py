from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast_lane_v2_2_2

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "backend" / "scripts" / "run_worker.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_worker_test_module",
        SCRIPT_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_worker_queue_isolation(monkeypatch):
    run_worker = _load_module()
    calls = []
    monkeypatch.setattr(run_worker, "record_worker_poll", lambda worker_id: None)
    monkeypatch.setattr(run_worker, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        run_worker,
        "_run_outbound",
        lambda worker_id: calls.append("outbound") or 1,
    )
    monkeypatch.setattr(
        run_worker,
        "_run_background",
        lambda worker_id: calls.append("background") or 1,
    )
    monkeypatch.setattr(
        run_worker,
        "_run_handoff_snapshot",
        lambda worker_id: calls.append("handoff-snapshot") or 1,
    )
    monkeypatch.setattr(
        run_worker,
        "_run_webchat_ai",
        lambda worker_id: calls.append("webchat-ai") or 1,
    )

    expectations = {
        "handoff-snapshot": ["handoff-snapshot"],
        "outbound": ["outbound"],
        "background": ["background"],
        "webchat-ai": ["webchat-ai"],
    }

    for queue, expected in expectations.items():
        calls.clear()
        processed = run_worker.run_queue_once("worker-test", queue)
        assert calls == expected
        assert processed == len(expected)

    calls.clear()
    with pytest.raises(ValueError, match="unsupported worker queue: all"):
        run_worker.run_queue_once("worker-test", "all")
    assert calls == []


def test_webchat_ai_reconciler_idle_cycle_does_not_log_info(monkeypatch):
    run_worker = _load_module()
    logs = []

    class FakeDb:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(run_worker, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(
        run_worker,
        "reconcile_webchat_ai_state",
        lambda db: {
            "inspected": 0,
            "cleared": 0,
            "failed": 0,
            "promoted": 0,
            "timed_out": 0,
        },
    )
    monkeypatch.setattr(
        run_worker,
        "record_queue_snapshot",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        run_worker.LOGGER,
        "info",
        lambda *a, **k: logs.append((a, k)),
    )

    assert (
        run_worker._run_webchat_ai_reconciler_watchdog(
            "worker-webchat-ai-candidate"
        )
        == 0
    )
    assert logs == []


def test_webchat_ai_worker_claims_one_llm_turn_per_cycle(monkeypatch):
    run_worker = _load_module()
    captured = {}

    class FakeDb:
        pass

    class FakeContext:
        def __enter__(self):
            return FakeDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_dispatch(db, *, limit=None, worker_id=None):
        captured["limit"] = limit
        captured["worker_id"] = worker_id
        return [object()]

    monkeypatch.setattr(run_worker, "db_context", lambda: FakeContext())
    monkeypatch.setattr(
        run_worker,
        "dispatch_pending_webchat_ai_reply_jobs",
        fake_dispatch,
    )
    monkeypatch.setattr(run_worker, "record_worker_result", lambda *a, **k: None)
    monkeypatch.setattr(run_worker, "record_queue_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        run_worker,
        "_run_webchat_ai_reconciler_watchdog",
        lambda worker_id: 0,
    )
    monkeypatch.setattr(
        run_worker,
        "_webchat_ai_reconciler_interval_seconds",
        lambda: 999999,
    )
    run_worker._LAST_WEBCHAT_AI_RECONCILER_RUN_AT = 0.0

    assert run_worker._run_webchat_ai("worker-a") == 1
    assert captured == {"limit": 1, "worker_id": "worker-a"}
