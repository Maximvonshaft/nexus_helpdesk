from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast_lane_v2_2_2

SCRIPT_PATH = Path(__file__).resolve().parents[2] / 'backend' / 'scripts' / 'run_worker.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('run_worker_test_module', SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_worker_queue_isolation(monkeypatch):
    run_worker = _load_module()
    calls = []
    monkeypatch.setattr(run_worker, 'record_worker_poll', lambda worker_id: None)
    monkeypatch.setattr(run_worker, 'log_event', lambda *a, **k: None)
    monkeypatch.setattr(run_worker, '_run_outbound', lambda worker_id: calls.append('outbound') or 1)
    monkeypatch.setattr(run_worker, '_run_openclaw_inbound', lambda worker_id: calls.append('openclaw-inbound') or 1)
    monkeypatch.setattr(run_worker, '_run_background', lambda worker_id: calls.append('background') or 1)
    monkeypatch.setattr(run_worker, '_run_handoff_snapshot', lambda worker_id: calls.append('handoff-snapshot') or 1)
    monkeypatch.setattr(run_worker, '_run_webchat_ai', lambda worker_id: calls.append('webchat-ai') or 1)

    expectations = {
        'handoff-snapshot': ['handoff-snapshot'],
        'outbound': ['outbound'],
        'background': ['background'],
        'openclaw-inbound': ['openclaw-inbound'],
        'webchat-ai': ['webchat-ai'],
        'all': ['outbound', 'openclaw-inbound', 'background', 'handoff-snapshot'],
    }

    for queue, expected in expectations.items():
        calls.clear()
        processed = run_worker.run_queue_once('worker-test', queue)
        assert calls == expected
        assert processed == len(expected)
