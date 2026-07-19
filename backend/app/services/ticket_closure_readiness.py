from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..enums import EventType, JobStatus, MessageStatus, TicketStatus
from ..models import BackgroundJob, Ticket, TicketEvent, TicketOutboundMessage, User
from ..utils.time import ensure_utc, utc_now
from .nexus_osr.business_scenarios import (
    BusinessScenarioCatalogError,
    BusinessScenarioDefinition,
    ScenarioReadiness,
    evaluate_scenario_readiness,
    load_business_scenario_catalog,
    resolve_business_scenario,
)
from .permissions import ensure_can_change_status, ensure_ticket_visible

CLOSURE_EVIDENCE_SCHEMA = "nexus.ticket-closure-evidence.v1"
CLOSURE_RECEIPT_SCHEMA = "nexus.ticket-closure-receipt.v1"
CLOSURE_EVIDENCE_FIELD = "closure_evidence"
CLOSURE_RECEIPT_FIELD = "closure_readiness_receipt"
CLOSURE_RECEIPT_INVALIDATED_FIELD = "closure_readiness_receipt_invalidated"

_ALLOWED_EVIDENCE_KINDS = frozenset({"fact", "customer_input", "action", "outcome", "notification"})
_ALLOWED_EVIDENCE_STATES = frozenset({"verified", "completed", "waived", "failed"})
_ALLOWED_SOURCE_KINDS = frozenset(
    {
        "tracking",
        "provider_receipt",
        "operations_dispatch",
        "customer_confirmation",
        "policy_decision",
        "operator_observation",
    }
)
_AUTHORITATIVE_FACT_SOURCE_KINDS = frozenset(
    {"tracking", "provider_receipt", "operations_dispatch", "customer_confirmation", "policy_decision"}
)
_BUSINESS_OUTCOME_SOURCE_KINDS = frozenset(
    {"provider_receipt", "operations_dispatch", "customer_confirmation", "policy_decision"}
)
_SUCCESSFUL_NOTIFICATION_STATES = frozenset({"accepted", "delivered", "opened", "sent"})
_FAILURE_NOTIFICATION_STATES = frozenset({"bounced", "failed", "rejected", "complained", "dead"})


@dataclass(frozen=True)
class ClosureSnapshot:
    scenario: BusinessScenarioDefinition | None
    readiness: ScenarioReadiness
    receipt: dict[str, Any]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_event_payload(event: TicketEvent) -> dict[str, Any]:
    try:
        value = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _scenario_identity(ticket: Ticket) -> tuple[str | None, str | None]:
    candidates = (
        ticket.case_type,
        ticket.sub_category,
        ticket.category,
        ticket.ai_classification,
    )
    for value in candidates:
        normalized = str(value or "").strip().lower()
        if normalized:
            return normalized, normalized
    return None, None


def _resolve_scenario(ticket: Ticket) -> tuple[BusinessScenarioDefinition | None, str | None, str | None, str | None]:
    try:
        catalog = load_business_scenario_catalog()
    except BusinessScenarioCatalogError as exc:
        return None, None, None, exc.reason
    scenario_key, issue_type = _scenario_identity(ticket)
    if not scenario_key:
        return None, catalog.catalog_version, catalog.source_sha256, "scenario_identity_missing"
    try:
        scenario = resolve_business_scenario(catalog, scenario_key=scenario_key, issue_type=issue_type)
    except BusinessScenarioCatalogError:
        try:
            scenario = resolve_business_scenario(catalog, issue_type=issue_type)
        except BusinessScenarioCatalogError as exc:
            return None, catalog.catalog_version, catalog.source_sha256, exc.reason
    return scenario, catalog.catalog_version, catalog.source_sha256, None


def _latest_explicit_evidence(events: Iterable[TicketEvent]) -> dict[tuple[str, str], tuple[TicketEvent, dict[str, Any]]]:
    latest: dict[tuple[str, str], tuple[TicketEvent, dict[str, Any]]] = {}
    for event in events:
        if event.field_name != CLOSURE_EVIDENCE_FIELD:
            continue
        payload = _parse_event_payload(event)
        if payload.get("schema") != CLOSURE_EVIDENCE_SCHEMA:
            continue
        kind = str(payload.get("kind") or "").strip().lower()
        key = str(payload.get("key") or "").strip().lower()
        if kind not in _ALLOWED_EVIDENCE_KINDS or not key:
            continue
        latest[(kind, key)] = (event, payload)
    return latest


def _job_ids_from_events(events: Iterable[TicketEvent]) -> set[int]:
    result: set[int] = set()
    for event in events:
        value = _parse_event_payload(event).get("job_id")
        if isinstance(value, int) and value > 0:
            result.add(value)
    return result


def _jobs_by_id(db: Session, job_ids: set[int]) -> dict[int, BackgroundJob]:
    if not job_ids:
        return {}
    return {
        row.id: row
        for row in db.query(BackgroundJob).filter(BackgroundJob.id.in_(sorted(job_ids))).all()
    }


def _event_action_projection(
    events: list[TicketEvent],
    jobs: dict[int, BackgroundJob],
) -> tuple[set[str], set[str], bool, list[int], datetime | None]:
    actions: set[str] = set()
    outcomes: set[str] = set()
    repair_required = False
    evidence_job_ids: list[int] = []
    latest_material_at: datetime | None = None

    for event in events:
        payload = _parse_event_payload(event)
        job_id = payload.get("job_id") if isinstance(payload.get("job_id"), int) else None
        job = jobs.get(job_id) if job_id else None
        completed = job is None or job.status == JobStatus.done
        failed = job is not None and job.status in {JobStatus.failed, JobStatus.dead}
        if job is not None:
            evidence_job_ids.append(job.id)
        if failed:
            repair_required = True

        if event.field_name == "speedaf_waybill_lookup" and event.new_value == "completed":
            actions.add("tracking_lookup")
            outcomes.add("technical_completed")
        elif event.field_name == "speedaf_work_order" and completed:
            actions.add("create_delivery_work_order")
            outcomes.update({"accepted", "technical_completed"})
        elif event.field_name == "speedaf_address_update" and completed:
            actions.add("update_address_contact")
            outcomes.update({"accepted", "technical_completed"})
        elif event.field_name == "speedaf_cancel" and completed:
            actions.add("cancel_order")
            outcomes.update({"accepted", "technical_completed"})
        elif event.event_type == EventType.escalated:
            actions.add("handoff")
        elif event.event_type == EventType.internal_note_added:
            actions.add("internal_note")

        if event.field_name in {
            "speedaf_waybill_lookup",
            "speedaf_work_order",
            "speedaf_address_update",
            "speedaf_cancel",
            CLOSURE_EVIDENCE_FIELD,
        }:
            observed = ensure_utc(event.created_at)
            if latest_material_at is None or observed > latest_material_at:
                latest_material_at = observed

    return actions, outcomes, repair_required, sorted(set(evidence_job_ids)), latest_material_at


def _explicit_projection(
    explicit: dict[tuple[str, str], tuple[TicketEvent, dict[str, Any]]],
) -> tuple[set[str], set[str], set[str], set[str], str | None, bool, list[int], datetime | None]:
    facts: set[str] = set()
    inputs: set[str] = set()
    actions: set[str] = set()
    outcomes: set[str] = set()
    notification: str | None = None
    repair_required = False
    event_ids: list[int] = []
    latest_at: datetime | None = None
    for (kind, key), (event, payload) in explicit.items():
        state = str(payload.get("state") or "").strip().lower()
        event_ids.append(event.id)
        observed = ensure_utc(event.created_at)
        if latest_at is None or observed > latest_at:
            latest_at = observed
        if state == "failed":
            repair_required = True
            continue
        if state not in {"verified", "completed", "waived"}:
            continue
        if kind == "fact":
            facts.add(key)
        elif kind == "customer_input":
            inputs.add(key)
        elif kind == "action":
            actions.add(key)
        elif kind == "outcome":
            outcomes.add(key)
        elif kind == "notification":
            notification = key if state != "waived" else f"waived:{key}"
    return facts, inputs, actions, outcomes, notification, repair_required, sorted(event_ids), latest_at


def _field_projection(ticket: Ticket) -> tuple[set[str], set[str]]:
    facts: set[str] = set()
    inputs: set[str] = set()
    if str(ticket.tracking_number or "").strip():
        facts.add("parcel_identity")
        inputs.add("tracking_reference")
    if ticket.events or ticket.comments or ticket.internal_notes:
        facts.add("case_history")
    customer = ticket.customer
    if customer and (str(customer.phone or "").strip() or str(customer.email or "").strip()):
        facts.add("address_contact")
    if str(ticket.preferred_reply_contact or "").strip() or str(ticket.customer_update or "").strip():
        inputs.add("corrected_contact")
    if str(ticket.customer_request or "").strip() or str(ticket.required_action or "").strip():
        inputs.add("request_reason")
    if str(ticket.issue_summary or ticket.description or "").strip():
        inputs.update({"complaint_summary", "claim_summary"})
    if str(ticket.resolution_summary or "").strip():
        inputs.add("requested_remedy")
    return facts, inputs


def _notification_projection(
    outbound: list[TicketOutboundMessage],
) -> tuple[str, set[str], set[str], bool, list[int], datetime | None]:
    notification_state = "not_required"
    actions: set[str] = set()
    outcomes: set[str] = set()
    repair_required = False
    ids: list[int] = []
    latest_at: datetime | None = None
    for row in outbound:
        ids.append(row.id)
        status = row.status.value if hasattr(row.status, "value") else str(row.status)
        delivery = str(row.delivery_status or "").strip().lower()
        observed = ensure_utc(row.delivery_receipt_at or row.sent_at or row.updated_at or row.created_at)
        if latest_at is None or observed > latest_at:
            latest_at = observed
        if status in {MessageStatus.failed.value, MessageStatus.dead.value} or delivery in _FAILURE_NOTIFICATION_STATES:
            repair_required = True
        if delivery in _SUCCESSFUL_NOTIFICATION_STATES or status == MessageStatus.sent.value:
            actions.add("notify_customer")
            outcomes.add("customer_notified")
            if delivery == "delivered":
                notification_state = "delivered"
            elif notification_state != "delivered":
                notification_state = "sent"
    return notification_state, actions, outcomes, repair_required, sorted(ids), latest_at


def _not_ready(reason: str) -> ScenarioReadiness:
    return ScenarioReadiness(
        scenario_key="unresolved",
        closure_ready=False,
        missing_fact_classes=(),
        missing_customer_inputs=(),
        missing_action_classes=(),
        missing_outcome_levels=(),
        notification_satisfied=False,
        blocked_reasons=(reason,),
    )


def build_closure_snapshot(db: Session, ticket: Ticket, *, now: datetime | None = None) -> ClosureSnapshot:
    observed_now = ensure_utc(now or utc_now())
    scenario, catalog_version, catalog_sha256, scenario_error = _resolve_scenario(ticket)
    events = (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.id.asc())
        .all()
    )
    outbound = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket.id)
        .order_by(TicketOutboundMessage.id.asc())
        .all()
    )
    explicit = _latest_explicit_evidence(events)
    jobs = _jobs_by_id(db, _job_ids_from_events(events))

    field_facts, field_inputs = _field_projection(ticket)
    event_actions, event_outcomes, event_repair, job_ids, event_at = _event_action_projection(events, jobs)
    (
        explicit_facts,
        explicit_inputs,
        explicit_actions,
        explicit_outcomes,
        explicit_notification,
        explicit_repair,
        explicit_event_ids,
        explicit_at,
    ) = _explicit_projection(explicit)
    notification, notification_actions, notification_outcomes, notification_repair, outbound_ids, outbound_at = (
        _notification_projection(outbound)
    )
    if explicit_notification:
        notification = explicit_notification

    latest_material_at = max(
        (value for value in (event_at, explicit_at, outbound_at, ensure_utc(ticket.resolved_at) if ticket.resolved_at else None) if value),
        default=None,
    )
    observation_elapsed = bool(
        scenario
        and (
            scenario.observation_period_seconds <= 0
            or (
                latest_material_at is not None
                and observed_now >= latest_material_at + timedelta(seconds=scenario.observation_period_seconds)
            )
        )
    )
    repair_required = event_repair or explicit_repair or notification_repair
    open_high_risk_escalation = ticket.status == TicketStatus.escalated

    if scenario is None:
        readiness = _not_ready(scenario_error or "scenario_unavailable")
    else:
        readiness = evaluate_scenario_readiness(
            scenario,
            available_fact_classes=field_facts | explicit_facts,
            available_customer_inputs=field_inputs | explicit_inputs,
            completed_action_classes=event_actions | explicit_actions | notification_actions | {"create_ticket"},
            completed_outcome_levels=event_outcomes | explicit_outcomes | notification_outcomes,
            customer_notification_state=notification,
            observation_period_elapsed=observation_elapsed,
            repair_required=repair_required,
            open_high_risk_escalation=open_high_risk_escalation,
        )

    receipt_without_hash: dict[str, Any] = {
        "schema": CLOSURE_RECEIPT_SCHEMA,
        "ticket_id": ticket.id,
        "ticket_status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
        "ticket_revision": ensure_utc(ticket.updated_at).isoformat(),
        "scenario_key": scenario.scenario_key if scenario else None,
        "scenario_catalog_version": catalog_version,
        "scenario_catalog_sha256": catalog_sha256,
        "generated_at": observed_now.isoformat(),
        "readiness": readiness.as_dict(),
        "evidence": {
            "ticket_event_ids": explicit_event_ids,
            "background_job_ids": job_ids,
            "outbound_message_ids": outbound_ids,
            "latest_material_at": latest_material_at.isoformat() if latest_material_at else None,
            "observation_elapsed": observation_elapsed,
            "contains_payloads": False,
        },
    }
    receipt = {**receipt_without_hash, "receipt_sha256": _sha256(receipt_without_hash)}
    return ClosureSnapshot(scenario=scenario, readiness=readiness, receipt=receipt)


def require_closure_ready(db: Session, ticket: Ticket) -> dict[str, Any]:
    snapshot = build_closure_snapshot(db, ticket)
    if not snapshot.readiness.closure_ready:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={
                "code": "safe_closure_not_ready",
                "scenario_key": snapshot.receipt.get("scenario_key"),
                "readiness": snapshot.readiness.as_dict(),
                "receipt_sha256": snapshot.receipt["receipt_sha256"],
            },
        )
    return snapshot.receipt


def append_closure_receipt_event(
    db: Session,
    *,
    ticket_id: int,
    actor_id: int | None,
    receipt: dict[str, Any],
) -> TicketEvent:
    row = TicketEvent(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=EventType.status_changed,
        field_name=CLOSURE_RECEIPT_FIELD,
        new_value=str(receipt["receipt_sha256"]),
        note="Safe Effective Closure receipt persisted.",
        payload_json=_canonical_json(receipt),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def invalidate_latest_closure_receipt(
    db: Session,
    *,
    ticket_id: int,
    actor_id: int | None,
    reason: str,
) -> TicketEvent | None:
    prior = (
        db.query(TicketEvent)
        .filter(
            TicketEvent.ticket_id == ticket_id,
            TicketEvent.field_name == CLOSURE_RECEIPT_FIELD,
        )
        .order_by(TicketEvent.id.desc())
        .first()
    )
    if prior is None:
        return None
    row = TicketEvent(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=EventType.reopened,
        field_name=CLOSURE_RECEIPT_INVALIDATED_FIELD,
        old_value=prior.new_value,
        new_value="invalidated",
        note=reason,
        payload_json=_canonical_json(
            {
                "schema": "nexus.ticket-closure-receipt-invalidation.v1",
                "receipt_event_id": prior.id,
                "receipt_sha256": prior.new_value,
                "reason": reason,
            }
        ),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def record_closure_evidence(
    db: Session,
    *,
    ticket: Ticket,
    current_user: User,
    kind: str,
    key: str,
    state: str,
    source_kind: str,
    source_ref: str,
    source_revision: str,
    observed_at: datetime,
    note: str | None = None,
) -> TicketEvent:
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_change_status(current_user, ticket, TicketStatus.closed, db)
    normalized_kind = str(kind or "").strip().lower()
    normalized_key = str(key or "").strip().lower()
    normalized_state = str(state or "").strip().lower()
    normalized_source = str(source_kind or "").strip().lower()
    if normalized_kind not in _ALLOWED_EVIDENCE_KINDS:
        raise ValueError("closure_evidence_kind_invalid")
    if normalized_state not in _ALLOWED_EVIDENCE_STATES:
        raise ValueError("closure_evidence_state_invalid")
    if normalized_source not in _ALLOWED_SOURCE_KINDS:
        raise ValueError("closure_evidence_source_invalid")
    if normalized_kind == "fact" and normalized_source not in _AUTHORITATIVE_FACT_SOURCE_KINDS:
        raise ValueError("closure_fact_source_not_authoritative")
    if normalized_kind == "outcome" and normalized_key == "business_result_confirmed" and normalized_source not in _BUSINESS_OUTCOME_SOURCE_KINDS:
        raise ValueError("closure_business_outcome_source_not_authoritative")
    if not normalized_key or not str(source_ref or "").strip() or not str(source_revision or "").strip():
        raise ValueError("closure_evidence_source_identity_required")

    scenario, _, _, error = _resolve_scenario(ticket)
    if scenario is None:
        raise ValueError(error or "scenario_unavailable")
    allowed_keys = {
        "fact": set(scenario.required_fact_classes),
        "customer_input": set(scenario.required_customer_inputs),
        "action": set(scenario.allowed_action_classes),
        "outcome": set(scenario.required_outcome_levels),
        "notification": set(scenario.allowed_no_notification_reasons) | {"sent", "delivered", "not_required", "prohibited"},
    }
    if normalized_key not in allowed_keys[normalized_kind]:
        raise ValueError("closure_evidence_key_not_in_scenario_contract")

    payload_without_hash = {
        "schema": CLOSURE_EVIDENCE_SCHEMA,
        "kind": normalized_kind,
        "key": normalized_key,
        "state": normalized_state,
        "source_kind": normalized_source,
        "source_ref": str(source_ref).strip()[:200],
        "source_revision": str(source_revision).strip()[:160],
        "observed_at": ensure_utc(observed_at).isoformat(),
        "recorded_by": current_user.id,
        "contains_payloads": False,
    }
    payload = {**payload_without_hash, "evidence_sha256": _sha256(payload_without_hash)}
    row = TicketEvent(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.field_updated,
        field_name=CLOSURE_EVIDENCE_FIELD,
        new_value=f"{normalized_kind}:{normalized_key}:{normalized_state}",
        note=(str(note).strip()[:500] if note else "Closure evidence recorded."),
        payload_json=_canonical_json(payload),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row
