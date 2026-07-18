#!/usr/bin/env python3
"""Evaluate infrastructure changes from sanitized qualification evidence only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path | None, *, label: str) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label}_missing")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"{label}_too_large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}_root_invalid")
    return payload


def _number(mapping: dict[str, Any] | None, *path: str) -> float | None:
    value: Any = mapping
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _bool(mapping: dict[str, Any] | None, *path: str) -> bool | None:
    value: Any = mapping
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, bool) else None


def evaluate(
    *,
    database: dict[str, Any] | None,
    queue: dict[str, Any] | None,
    realtime: dict[str, Any] | None,
    storage: dict[str, Any] | None,
) -> dict[str, Any]:
    decisions: dict[str, Any] = {}

    db_within_budget = _bool(database, "budget", "within_budget")
    db_budget_percent = _number(database, "budget", "budget_percent")
    db_pool_wait_p95 = _number(database, "runtime", "pool_checkout_wait_p95_ms")
    db_polling_share = _number(database, "runtime", "polling_broker_total_exec_time_percent")

    pgbouncer_reasons: list[str] = []
    if database is None:
        pgbouncer_reasons.append("database_baseline_missing")
    if db_within_budget is not True:
        pgbouncer_reasons.append("application_connection_budget_not_proven")
    if db_pool_wait_p95 is None:
        pgbouncer_reasons.append("pool_checkout_wait_not_measured")
    pgbouncer_decision = "HOLD"
    if not pgbouncer_reasons and db_pool_wait_p95 > 20 and (db_budget_percent or 0) >= 70:
        pgbouncer_decision = "CONSIDER_ADR"
        pgbouncer_reasons.append("connection_fragmentation_or_wait_confirmed")
    elif not pgbouncer_reasons:
        pgbouncer_decision = "NO_CHANGE"
        pgbouncer_reasons.append("application_pool_budget_and_wait_are_acceptable")
    decisions["pgbouncer"] = {
        "decision": pgbouncer_decision,
        "reason_codes": sorted(set(pgbouncer_reasons)),
        "activation_authorized": False,
    }

    realtime_required = _bool(realtime, "multi_instance_fanout_required")
    reconnect_slo_breached = _bool(realtime, "reconnect_storm_slo_breached")
    redis_reasons: list[str] = []
    if realtime is None:
        redis_reasons.append("realtime_baseline_missing")
    if db_polling_share is None:
        redis_reasons.append("database_polling_cost_not_measured")
    redis_decision = "HOLD"
    if not redis_reasons and (
        realtime_required is True
        or reconnect_slo_breached is True
        or db_polling_share > 10
    ):
        redis_decision = "CONSIDER_ADR"
        redis_reasons.append("fanout_or_database_polling_bottleneck_confirmed")
    elif not redis_reasons:
        redis_decision = "NO_CHANGE"
        redis_reasons.append("database_backed_realtime_path_within_budget")
    decisions["redis"] = {
        "decision": redis_decision,
        "reason_codes": sorted(set(redis_reasons)),
        "activation_authorized": False,
    }

    storage_backend = (storage or {}).get("backend") if isinstance(storage, dict) else None
    storage_status = (storage or {}).get("status") if isinstance(storage, dict) else None
    multi_writer_required = _bool(storage, "baseline", "multi_writer_required")
    rpo_rto_met = _bool(storage, "baseline", "rpo_rto_met")
    capacity_breached = _bool(storage, "baseline", "capacity_boundary_breached")
    object_reasons: list[str] = []
    if storage is None:
        object_reasons.append("storage_baseline_missing")
    if storage_backend == "s3" and storage_status == "ok":
        object_decision = "NO_CHANGE"
        object_reasons.append("object_storage_already_authoritative")
    elif storage is not None and (
        multi_writer_required is True
        or rpo_rto_met is False
        or capacity_breached is True
    ):
        object_decision = "CONSIDER_ADR"
        object_reasons.append("local_storage_boundary_confirmed")
    elif (
        storage_backend == "local"
        and storage_status == "ok"
        and multi_writer_required is False
        and rpo_rto_met is True
        and capacity_breached is False
    ):
        object_decision = "NO_CHANGE"
        object_reasons.append("pilot_local_storage_boundary_not_exceeded")
    else:
        object_decision = "CONDITIONAL_HOLD"
        if storage is not None and not object_reasons:
            object_reasons.append("storage_boundary_evidence_incomplete")
    decisions["object_storage"] = {
        "decision": object_decision,
        "reason_codes": sorted(set(object_reasons)),
        "migration_authorized": False,
    }

    queue_status = (queue or {}).get("status") if isinstance(queue, dict) else None
    oldest_job = _number(queue, "background_jobs", "oldest_pending_age_ms")
    oldest_outbound = _number(queue, "outbound", "oldest_pending_age_ms")
    stale_jobs = _number(queue, "background_jobs", "stale_processing")
    stale_outbound = _number(queue, "outbound", "stale_processing")
    worker_busy_ratio = _number(queue, "baseline", "worker_busy_ratio_percent")
    cpu_headroom = _number(queue, "baseline", "cpu_headroom_percent")
    db_headroom = None if db_budget_percent is None else 100 - db_budget_percent
    worker_reasons: list[str] = []
    if queue is None:
        worker_reasons.append("queue_baseline_missing")
    if stale_jobs not in {None, 0} or stale_outbound not in {None, 0}:
        worker_reasons.append("stale_processing_present")
    if worker_busy_ratio is None:
        worker_reasons.append("worker_busy_ratio_not_measured")
    if cpu_headroom is None:
        worker_reasons.append("cpu_headroom_not_measured")
    if db_headroom is None:
        worker_reasons.append("database_headroom_not_measured")
    worker_decision = "BLOCKED"
    ready_age_breached = max(oldest_job or 0, oldest_outbound or 0) > 300_000
    if not worker_reasons and queue_status == "not_ready":
        worker_reasons.append("queue_business_health_not_ready")
    if not worker_reasons and ready_age_breached and worker_busy_ratio > 70 and cpu_headroom >= 30 and db_headroom >= 20:
        worker_decision = "CONSIDER_ADR"
        worker_reasons.append("sustained_queue_lag_with_resource_headroom")
    elif not worker_reasons:
        worker_decision = "NO_CHANGE"
        worker_reasons.append("queue_slo_or_utilization_does_not_justify_scale_out")
    decisions["additional_workers"] = {
        "decision": worker_decision,
        "reason_codes": sorted(set(worker_reasons)),
        "scale_out_authorized": False,
    }

    return {
        "schema": "nexus.infrastructure-decision.v1",
        "status": "evidence_complete"
        if all(
            item["decision"] in {"NO_CHANGE", "CONSIDER_ADR"}
            for item in decisions.values()
        )
        else "evidence_incomplete",
        "decisions": decisions,
        "automatic_change_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--realtime", type=Path)
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = evaluate(
        database=_load(args.database, label="database") if args.database else None,
        queue=_load(args.queue, label="queue") if args.queue else None,
        realtime=_load(args.realtime, label="realtime") if args.realtime else None,
        storage=_load(args.storage, label="storage") if args.storage else None,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "evidence_complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
