from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "infrastructure_decision",
    ROOT / "scripts" / "qualification" / "infrastructure_decision.py",
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_missing_baselines_hold_every_infrastructure_change():
    result = module.evaluate(database=None, queue=None, realtime=None, storage=None)

    assert result["status"] == "evidence_incomplete"
    assert result["automatic_change_authorized"] is False
    assert result["decisions"]["pgbouncer"]["decision"] == "HOLD"
    assert result["decisions"]["redis"]["decision"] == "HOLD"
    assert result["decisions"]["object_storage"]["decision"] == "CONDITIONAL_HOLD"
    assert result["decisions"]["additional_workers"]["decision"] == "BLOCKED"


def test_healthy_baseline_produces_no_change():
    result = module.evaluate(
        database={
            "budget": {"within_budget": True, "budget_percent": 35},
            "runtime": {
                "pool_checkout_wait_p95_ms": 3,
                "polling_broker_total_exec_time_percent": 2,
            },
        },
        queue={
            "status": "ready",
            "background_jobs": {"oldest_pending_age_ms": 1000, "stale_processing": 0},
            "outbound": {"oldest_pending_age_ms": 1000, "stale_processing": 0},
            "baseline": {"worker_busy_ratio_percent": 40, "cpu_headroom_percent": 50},
        },
        realtime={
            "multi_instance_fanout_required": False,
            "reconnect_storm_slo_breached": False,
        },
        storage={
            "backend": "local",
            "status": "ok",
            "baseline": {
                "multi_writer_required": False,
                "rpo_rto_met": True,
                "capacity_boundary_breached": False,
            },
        },
    )

    assert result["status"] == "evidence_complete"
    assert result["decisions"]["pgbouncer"]["decision"] == "NO_CHANGE"
    assert result["decisions"]["redis"]["decision"] == "NO_CHANGE"
    assert result["decisions"]["object_storage"]["decision"] == "CONDITIONAL_HOLD"
    assert result["decisions"]["additional_workers"]["decision"] == "NO_CHANGE"


def test_confirmed_connection_wait_enters_pgbouncer_adr_only():
    result = module.evaluate(
        database={
            "budget": {"within_budget": True, "budget_percent": 75},
            "runtime": {
                "pool_checkout_wait_p95_ms": 35,
                "polling_broker_total_exec_time_percent": 2,
            },
        },
        queue={
            "status": "ready",
            "background_jobs": {"oldest_pending_age_ms": 1000, "stale_processing": 0},
            "outbound": {"oldest_pending_age_ms": 1000, "stale_processing": 0},
            "baseline": {"worker_busy_ratio_percent": 40, "cpu_headroom_percent": 50},
        },
        realtime={
            "multi_instance_fanout_required": False,
            "reconnect_storm_slo_breached": False,
        },
        storage={
            "backend": "local",
            "status": "ok",
            "baseline": {
                "multi_writer_required": False,
                "rpo_rto_met": True,
                "capacity_boundary_breached": False,
            },
        },
    )

    assert result["decisions"]["pgbouncer"]["decision"] == "CONSIDER_ADR"
    assert result["decisions"]["pgbouncer"]["activation_authorized"] is False
    assert result["decisions"]["redis"]["decision"] == "NO_CHANGE"


def test_worker_scale_out_requires_lag_utilization_and_headroom():
    result = module.evaluate(
        database={
            "budget": {"within_budget": True, "budget_percent": 60},
            "runtime": {
                "pool_checkout_wait_p95_ms": 5,
                "polling_broker_total_exec_time_percent": 2,
            },
        },
        queue={
            "status": "ready",
            "background_jobs": {"oldest_pending_age_ms": 600_000, "stale_processing": 0},
            "outbound": {"oldest_pending_age_ms": 1000, "stale_processing": 0},
            "baseline": {"worker_busy_ratio_percent": 85, "cpu_headroom_percent": 40},
        },
        realtime={
            "multi_instance_fanout_required": False,
            "reconnect_storm_slo_breached": False,
        },
        storage={
            "backend": "local",
            "status": "ok",
            "baseline": {
                "multi_writer_required": False,
                "rpo_rto_met": True,
                "capacity_boundary_breached": False,
            },
        },
    )

    assert result["decisions"]["additional_workers"]["decision"] == "CONSIDER_ADR"
    assert result["decisions"]["additional_workers"]["scale_out_authorized"] is False
