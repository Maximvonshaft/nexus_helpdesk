from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / 'scripts' / 'smoke' / 'worker_daemon_readiness_probe.py'


def _load_probe_module():
    spec = importlib.util.spec_from_file_location('worker_daemon_readiness_probe', SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_refuses_destructive_arguments() -> None:
    probe = _load_probe_module()
    with pytest.raises(SystemExit):
        probe._guard_read_only(['restart'])


def test_service_state_detects_running_compose_service() -> None:
    probe = _load_probe_module()
    ps = {'ok': True, 'services': [{'Service': 'worker', 'State': 'running'}]}
    state = probe._service_state(ps, 'worker')
    assert state['found'] is True
    assert state['running'] is True


def test_service_state_reports_missing() -> None:
    probe = _load_probe_module()
    state = probe._service_state({'ok': True, 'services': []}, 'event-daemon')
    assert state == {'found': False, 'running': False}
