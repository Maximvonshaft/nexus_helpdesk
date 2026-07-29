from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from ..models import Ticket, TicketOutboundMessage
from ..models_case_governance import CaseOutcomeRecord
from .nexus_osr.business_scenarios import ScenarioReadiness
from .ticket_closure_readiness import ClosureSnapshot, build_closure_snapshot

_CONFIRMED_OUTBOUND_STATES = frozenset(
    {"delivered", "confirmed", "opened", "read"}
)
_CONFIRMED_LEDGER_STATES = frozenset({"delivered", "confirmed"})
_ATTEMPT_LEDGER_STATES = frozenset(
    {"requested", "accepted", "processing", "succeeded"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _waiver_reason(row: CaseOutcomeRecord) -> str | None:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    raw = payload.get("waiver_reason") or payload.get("reason_code")
    value = str(raw or "").strip().lower()
    return value or None


def notification_evidence(
    db: Session,
    *,
    ticket_id: int,
    allowed_waiver_reasons: tuple[str, ...] = (),
) -> dict[str, Any]:
    ledger_rows = (
        db.query(CaseOutcomeRecord)
        .filter(
            CaseOutcomeRecord.ticket_id == ticket_id,
            CaseOutcomeRecord.record_type == "customer_notification",
        )
        .order_by(
            CaseOutcomeRecord.sequence.asc(),
            CaseOutcomeRecord.id.asc(),
        )
        .all()
    )
    confirmed_ledger_ids = [
        row.id
        for row in ledger_rows
        if row.state in _CONFIRMED_LEDGER_STATES
    ]
    attempted_ledger_ids = [
        row.id
        for row in ledger_rows
        if row.state in _ATTEMPT_LEDGER_STATES
    ]
    allowed_waivers = {
        str(value).strip().lower()
        for value in allowed_waiver_reasons
    }
    waived_rows = [
        row
        for row in ledger_rows
        if row.state == "waived"
        and _waiver_reason(row) in allowed_waivers
    ]

    outbound_rows = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket_id)
        .order_by(TicketOutboundMessage.id.asc())
        .all()
    )
    confirmed_outbound_ids = [
        row.id
        for row in outbound_rows
        if str(row.delivery_status or "").strip().lower()
        in _CONFIRMED_OUTBOUND_STATES
    ]
    attempted_outbound_ids = [row.id for row in outbound_rows]

    if confirmed_ledger_ids or confirmed_outbound_ids:
        state = "confirmed"
    elif waived_rows:
        state = "waived"
    elif attempted_ledger_ids or attempted_outbound_ids:
        state = "attempted"
    else:
        state = "missing"
    return {
        "schema": "nexus.notification-evidence.v1",
        "state": state,
        "confirmed": state == "confirmed",
        "waived": state == "waived",
        "confirmed_case_outcome_record_ids": confirmed_ledger_ids,
        "confirmed_outbound_message_ids": confirmed_outbound_ids,
        "waived_case_outcome_record_ids": [row.id for row in waived_rows],
        "attempted_case_outcome_record_ids": attempted_ledger_ids,
        "attempted_outbound_message_ids": attempted_outbound_ids,
        "contains_payloads": False,
    }


def _notification_satisfied(
    *,
    policy: str,
    evidence: dict[str, Any],
) -> bool:
    normalized = str(policy or "required").strip().lower()
    if normalized == "prohibited":
        return evidence["state"] == "missing"
    if normalized == "optional":
        return True
    if normalized == "required_if_contactable":
        return bool(evidence["confirmed"] or evidence["waived"])
    return bool(evidence["confirmed"])


def apply_notification_evidence_policy(
    db: Session,
    *,
    ticket: Ticket,
    snapshot: ClosureSnapshot,
) -> ClosureSnapshot:
    scenario = snapshot.scenario
    if scenario is None:
        return snapshot
    evidence = notification_evidence(
        db,
        ticket_id=ticket.id,
        allowed_waiver_reasons=scenario.allowed_no_notification_reasons,
    )
    satisfied = _notification_satisfied(
        policy=scenario.notification_policy,
        evidence=evidence,
    )
    existing = snapshot.readiness
    blocked = list(existing.blocked_reasons)
    if not satisfied and "notification_delivery_unconfirmed" not in blocked:
        blocked.append("notification_delivery_unconfirmed")
    if satisfied:
        blocked = [
            reason
            for reason in blocked
            if reason
            not in {
                "notification_requirement_unsatisfied",
                "notification_delivery_unconfirmed",
            }
        ]
    readiness = ScenarioReadiness(
        scenario_key=existing.scenario_key,
        closure_ready=not blocked,
        missing_fact_classes=existing.missing_fact_classes,
        missing_customer_inputs=existing.missing_customer_inputs,
        missing_action_classes=existing.missing_action_classes,
        missing_outcome_levels=existing.missing_outcome_levels,
        notification_satisfied=satisfied,
        blocked_reasons=tuple(blocked),
    )
    receipt_without_hash = {
        key: value
        for key, value in snapshot.receipt.items()
        if key != "receipt_sha256"
    }
    receipt_without_hash["readiness"] = readiness.as_dict()
    receipt_evidence = dict(receipt_without_hash.get("evidence") or {})
    receipt_evidence["notification"] = evidence
    receipt_without_hash["evidence"] = receipt_evidence
    receipt = {
        **receipt_without_hash,
        "receipt_sha256": _sha256(receipt_without_hash),
    }
    return replace(snapshot, readiness=readiness, receipt=receipt)


def build_governed_closure_snapshot(
    db: Session,
    ticket: Ticket,
    *,
    now=None,
) -> ClosureSnapshot:
    return apply_notification_evidence_policy(
        db,
        ticket=ticket,
        snapshot=build_closure_snapshot(db, ticket, now=now),
    )


def require_governed_closure_ready(
    db: Session,
    ticket: Ticket,
) -> dict[str, Any]:
    snapshot = build_governed_closure_snapshot(db, ticket)
    if not snapshot.readiness.closure_ready:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={
                "code": "safe_closure_not_ready",
                "scenario_key": snapshot.receipt.get("scenario_key"),
                "scenario_assignment_id": snapshot.receipt.get(
                    "scenario_assignment_id"
                ),
                "readiness": snapshot.readiness.as_dict(),
                "receipt_sha256": snapshot.receipt["receipt_sha256"],
            },
        )
    return snapshot.receipt
