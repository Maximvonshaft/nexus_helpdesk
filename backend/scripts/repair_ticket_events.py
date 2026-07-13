from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.db import SessionLocal
from app.models import TicketEvent
from app.models_osr import CaseContextRecord, RuntimeDecisionAuditRecord
from app.services.ticket_event_repair import (
    TicketEventRepairScopeError,
    apply_ticket_event_repairs,
    plan_ticket_event_repairs,
)
from sqlalchemy.orm import Session

_MAX_BATCH_SIZE = 1_000
_MAX_EVENTS = 100_000


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply a bounded, Tenant-scoped TicketEvent repair plan.",
    )
    parser.add_argument(
        "--tenant-id", required=True, help="Explicit non-default Tenant identity"
    )
    parser.add_argument("--batch-size", type=_positive_int, default=200)
    parser.add_argument("--after-id", type=_non_negative_int, default=0)
    parser.add_argument("--max-events", type=_positive_int, default=10_000)
    parser.add_argument(
        "--apply", action="store_true", help="Apply planned changes; default is dry-run"
    )
    parser.add_argument(
        "--output", type=Path, help="Optional path for the bounded JSON summary"
    )
    args = parser.parse_args(argv)
    if args.batch_size > _MAX_BATCH_SIZE:
        parser.error(f"--batch-size cannot exceed {_MAX_BATCH_SIZE}")
    if args.max_events > _MAX_EVENTS:
        parser.error(f"--max-events cannot exceed {_MAX_EVENTS}")
    return args


def resolve_authorized_ticket_ids(
    db: Session,
    tenant_id: str,
    *,
    lock_for_update: bool = False,
) -> frozenset[int]:
    normalized = str(tenant_id or "").strip()
    if not normalized or normalized.lower() == "default":
        raise TicketEventRepairScopeError(
            "tenant_id must be explicit and cannot use the default fallback"
        )

    case_query = db.query(CaseContextRecord.ticket_id).filter(
        CaseContextRecord.tenant_id == normalized,
        CaseContextRecord.ticket_id.is_not(None),
    )
    audit_query = db.query(RuntimeDecisionAuditRecord.ticket_id).filter(
        RuntimeDecisionAuditRecord.tenant_id == normalized,
        RuntimeDecisionAuditRecord.ticket_id.is_not(None),
    )
    if lock_for_update:
        case_query = case_query.with_for_update()
        audit_query = audit_query.with_for_update()
    case_ids = {
        int(ticket_id)
        for (ticket_id,) in case_query.all()
        if ticket_id is not None
    }
    audit_ids = {
        int(ticket_id)
        for (ticket_id,) in audit_query.all()
        if ticket_id is not None
    }
    ticket_ids = case_ids | audit_ids
    if not ticket_ids:
        raise TicketEventRepairScopeError(
            "no server-owned Ticket associations exist for this Tenant"
        )

    conflicting_case_query = db.query(CaseContextRecord.id).filter(
        CaseContextRecord.ticket_id.in_(ticket_ids),
        CaseContextRecord.tenant_id != normalized,
    )
    conflicting_audit_query = db.query(RuntimeDecisionAuditRecord.id).filter(
        RuntimeDecisionAuditRecord.ticket_id.in_(ticket_ids),
        RuntimeDecisionAuditRecord.tenant_id != normalized,
    )
    if lock_for_update:
        conflicting_case_query = conflicting_case_query.with_for_update()
        conflicting_audit_query = conflicting_audit_query.with_for_update()
    conflicting_case = conflicting_case_query.first()
    conflicting_audit = conflicting_audit_query.first()
    if conflicting_case is not None or conflicting_audit is not None:
        raise TicketEventRepairScopeError(
            "Tenant ticket scope is ambiguous across server-owned records"
        )
    return frozenset(sorted(ticket_ids))


def load_ticket_event_batch(
    db: Session,
    *,
    ticket_ids: frozenset[int],
    after_id: int,
    limit: int,
    lock_for_update: bool,
) -> list[TicketEvent]:
    query = (
        db.query(TicketEvent)
        .filter(
            TicketEvent.ticket_id.in_(ticket_ids),
            TicketEvent.id > after_id,
        )
        .order_by(TicketEvent.id.asc())
        .limit(limit)
    )
    if lock_for_update:
        query = query.with_for_update()
    return list(query.all())


def _write_summary(summary: dict[str, Any], output: Path | None) -> None:
    encoded = (
        json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    if len(encoded.encode("utf-8")) > 65_536:
        raise RuntimeError("repair summary exceeded the bounded output limit")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db = SessionLocal()
    class_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    scanned_count = 0
    changed_count = 0
    unclassified_count = 0
    first_event_id: int | None = None
    last_event_id: int | None = None
    cursor = args.after_id
    remaining = args.max_events

    try:
        ticket_ids = resolve_authorized_ticket_ids(db, args.tenant_id)
        while remaining > 0:
            limit = min(args.batch_size, remaining)
            if args.apply:
                current_ticket_ids = resolve_authorized_ticket_ids(
                    db,
                    args.tenant_id,
                    lock_for_update=True,
                )
                if current_ticket_ids != ticket_ids:
                    raise TicketEventRepairScopeError(
                        "Tenant ticket scope changed during repair"
                    )
            rows = load_ticket_event_batch(
                db,
                ticket_ids=ticket_ids,
                after_id=cursor,
                limit=limit,
                lock_for_update=args.apply,
            )
            if not rows:
                break
            plan = plan_ticket_event_repairs(
                rows,
                tenant_id=args.tenant_id,
                authorized_ticket_ids=set(ticket_ids),
            )
            scanned_count += plan.scanned_count
            changed_count += plan.changed_count
            unclassified_count += plan.unclassified_count
            class_counts.update(dict(plan.event_class_counts))
            reason_counts.update(dict(plan.reason_counts))
            if first_event_id is None:
                first_event_id = plan.first_event_id
            last_event_id = plan.last_event_id

            if args.apply and plan.changed_count:
                apply_ticket_event_repairs(rows, plan)
                db.commit()
            else:
                db.rollback()

            cursor = int(rows[-1].id)
            remaining -= len(rows)
            if len(rows) < limit:
                break

        summary = {
            "schema": "nexus.ticket_event.repair.execution.v1",
            "mode": "apply" if args.apply else "dry_run",
            "tenant_id": str(args.tenant_id).strip(),
            "authorized_ticket_count": len(ticket_ids),
            "scanned_count": scanned_count,
            "changed_count": changed_count,
            "unclassified_count": unclassified_count,
            "event_class_counts": dict(sorted(class_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "first_event_id": first_event_id,
            "last_event_id": last_event_id,
            "next_after_id": cursor,
            "truncated": remaining == 0,
        }
        _write_summary(summary, args.output)
        return 0
    except TicketEventRepairScopeError as exc:
        db.rollback()
        _write_summary(
            {
                "schema": "nexus.ticket_event.repair.execution.v1",
                "mode": "apply" if args.apply else "dry_run",
                "tenant_id": str(args.tenant_id or "").strip()[:80],
                "status": "scope_rejected",
                "error_code": type(exc).__name__,
            },
            args.output,
        )
        return 2
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(run())
