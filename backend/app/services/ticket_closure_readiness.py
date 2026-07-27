from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType, MessageStatus, TicketStatus
from ..models import Ticket, TicketEvent, TicketOutboundMessage, User
from ..models_case_governance import CaseOutcomeRecord
from ..utils.time import ensure_utc, utc_now
from .case_outcome_service import (
    append_case_outcome,
    project_case_ledger,
    record_case_evidence,
)
from .nexus_osr.business_scenarios import (
    BusinessScenarioCatalogError,
    BusinessScenarioDefinition,
    ScenarioReadiness,
    evaluate_scenario_readiness,
    load_business_scenario_catalog,
    resolve_business_scenario,
)
from .permissions import ensure_can_change_status, ensure_ticket_visible

CLOSURE_EVIDENCE_SCHEMA = "nexus.case-closure-evidence.v2"
CLOSURE_RECEIPT_SCHEMA = "nexus.ticket-closure-receipt.v2"
CLOSURE_RECEIPT_FIELD = "closure_readiness_receipt"
CLOSURE_RECEIPT_INVALIDATED_FIELD = "closure_readiness_receipt_invalidated"

_ALLOWED_EVIDENCE_KINDS = frozenset(
    {"fact", "customer_input", "action", "outcome", "notification"}
)
_ALLOWED_EVIDENCE_STATES = frozenset(
    {"verified", "completed", "waived", "failed"}
)
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
    {
        "tracking",
        "provider_receipt",
        "operations_dispatch",
        "customer_confirmation",
        "policy_decision",
    }
)
_BUSINESS_OUTCOME_SOURCE_KINDS = frozenset(
    {
        "provider_receipt",
        "operations_dispatch",
        "customer_confirmation",
        "policy_decision",
    }
)
_NOTIFICATION_ATTEMPT_STATES = frozenset(
    {"accepted", "pending", "queued", "sent"}
)
_NOTIFICATION_CONFIRMED_STATES = frozenset(
    {"confirmed", "delivered", "opened", "read"}
)
_NOTIFICATION_FAILURE_STATES = frozenset(
    {"bounced", "failed", "rejected", "complained", "dead"}
)


@dataclass(frozen=True)
class ClosureSnapshot:
    scenario: BusinessScenarioDefinition | None
    readiness: ScenarioReadiness
    receipt: dict[str, Any]


@dataclass(frozen=True)
class RecordedClosureEvidence:
    record_id: int
    record_type: str
    evidence_sha256: str
    created: bool


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


def _resolve_scenario(
    ticket: Ticket,
) -> tuple[
    BusinessScenarioDefinition | None,
    str | None,
    str | None,
    str | None,
]:
    try:
        catalog = load_business_scenario_catalog()
    except BusinessScenarioCatalogError as exc:
        return None, None, None, exc.reason
    scenario_key, issue_type = _scenario_identity(ticket)
    if not scenario_key:
        return (
            None,
            catalog.catalog_version,
            catalog.source_sha256,
            "scenario_identity_missing",
        )
    try:
        scenario = resolve_business_scenario(
            catalog,
            scenario_key=scenario_key,
            issue_type=issue_type,
        )
    except BusinessScenarioCatalogError:
        try:
            scenario = resolve_business_scenario(
                catalog,
                issue_type=issue_type,
            )
        except BusinessScenarioCatalogError as exc:
            return (
                None,
                catalog.catalog_version,
                catalog.source_sha256,
                exc.reason,
            )
    return scenario, catalog.catalog_version, catalog.source_sha256, None


def _field_projection(ticket: Ticket) -> tuple[set[str], set[str]]:
    """Project stable case identity and customer-provided inputs only."""

    facts: set[str] = set()
    inputs: set[str] = set()
    if str(ticket.tracking_number or "").strip():
        facts.add("parcel_identity")
        inputs.add("tracking_reference")
    if ticket.events or ticket.comments or ticket.internal_notes:
        facts.add("case_history")
    customer = ticket.customer
    if customer and (
        str(customer.phone or "").strip()
        or str(customer.email or "").strip()
    ):
        facts.add("address_contact")
    if (
        str(ticket.preferred_reply_contact or "").strip()
        or str(ticket.customer_update or "").strip()
    ):
        inputs.add("corrected_contact")
    if (
        str(ticket.customer_request or "").strip()
        or str(ticket.required_action or "").strip()
    ):
        inputs.add("request_reason")
    if str(ticket.issue_summary or ticket.description or "").strip():
        inputs.update({"complaint_summary", "claim_summary"})
    if str(ticket.resolution_summary or "").strip():
        inputs.add("requested_remedy")
    return facts, inputs


def _notification_projection(
    outbound: list[TicketOutboundMessage],
) -> tuple[str, set[str], set[str], bool, list[int], datetime | None]:
    """Project technical attempts separately from confirmed customer delivery."""

    notification_state = "not_required"
    actions: set[str] = set()
    outcomes: set[str] = set()
    repair_required = False
    ids: list[int] = []
    latest_at: datetime | None = None

    for row in outbound:
        ids.append(row.id)
        status = str(
            row.status.value if hasattr(row.status, "value") else row.status
        ).strip().lower()
        delivery = str(row.delivery_status or "").strip().lower()
        observed = ensure_utc(
            row.delivery_receipt_at
            or row.sent_at
            or row.updated_at
            or row.created_at
        )
        if observed is not None and (
            latest_at is None or observed > latest_at
        ):
            latest_at = observed

        if (
            status in {MessageStatus.failed.value, MessageStatus.dead.value}
            or delivery in _NOTIFICATION_FAILURE_STATES
        ):
            repair_required = True

        attempted = (
            status in {MessageStatus.pending.value, MessageStatus.sent.value}
            or delivery in _NOTIFICATION_ATTEMPT_STATES
            or delivery in _NOTIFICATION_CONFIRMED_STATES
        )
        confirmed = delivery in _NOTIFICATION_CONFIRMED_STATES
        if attempted:
            actions.add("notify_customer")
            if notification_state == "not_required":
                notification_state = "sent"
        if confirmed:
            outcomes.add("customer_notified")
            notification_state = "delivered"

    return (
        notification_state,
        actions,
        outcomes,
        repair_required,
        sorted(ids),
        latest_at,
    )


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


def build_closure_snapshot(
    db: Session,
    ticket: Ticket,
    *,
    now: datetime | None = None,
) -> ClosureSnapshot:
    """Build the base fact/action/outcome projection.

    Notification delivery strength is applied by
    ``notification_evidence_policy.build_governed_closure_snapshot``. This
    function is not itself a close authorization.
    """

    observed_now = ensure_utc(now or utc_now())
    if observed_now is None:
        raise ValueError("closure_time_unavailable")
    scenario, catalog_version, catalog_sha256, scenario_error = _resolve_scenario(
        ticket
    )
    ledger = project_case_ledger(db, ticket_id=ticket.id)
    outbound = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket.id)
        .order_by(TicketOutboundMessage.id.asc())
        .all()
    )

    field_facts, field_inputs = _field_projection(ticket)
    (
        notification,
        notification_actions,
        notification_outcomes,
        notification_repair,
        outbound_ids,
        outbound_at,
    ) = _notification_projection(outbound)
    if ledger.notification_state:
        notification = ledger.notification_state

    resolved_at = ensure_utc(ticket.resolved_at) if ticket.resolved_at else None
    latest_material_at = max(
        (
            value
            for value in (
                ledger.latest_material_at,
                outbound_at,
                resolved_at,
            )
            if value is not None
        ),
        default=None,
    )
    observation_elapsed = bool(
        scenario
        and (
            scenario.observation_period_seconds <= 0
            or (
                latest_material_at is not None
                and observed_now
                >= latest_material_at
                + timedelta(seconds=scenario.observation_period_seconds)
            )
        )
    )
    repair_required = ledger.repair_required or notification_repair
    open_high_risk_escalation = ticket.status == TicketStatus.escalated

    if scenario is None:
        readiness = _not_ready(scenario_error or "scenario_unavailable")
    else:
        readiness = evaluate_scenario_readiness(
            scenario,
            available_fact_classes=(
                field_facts | set(ledger.fact_classes)
            ),
            available_customer_inputs=(
                field_inputs | set(ledger.customer_inputs)
            ),
            completed_action_classes=(
                set(ledger.action_classes)
                | notification_actions
                | {"create_ticket"}
            ),
            completed_outcome_levels=(
                set(ledger.outcome_levels) | notification_outcomes
            ),
            customer_notification_state=notification,
            observation_period_elapsed=observation_elapsed,
            repair_required=repair_required,
            open_high_risk_escalation=open_high_risk_escalation,
        )

    ticket_revision = ensure_utc(ticket.updated_at)
    receipt_without_hash: dict[str, Any] = {
        "schema": CLOSURE_RECEIPT_SCHEMA,
        "ticket_id": ticket.id,
        "ticket_status": (
            ticket.status.value
            if hasattr(ticket.status, "value")
            else str(ticket.status)
        ),
        "ticket_revision": (
            ticket_revision.isoformat() if ticket_revision is not None else None
        ),
        "scenario_key": scenario.scenario_key if scenario else None,
        "scenario_catalog_version": catalog_version,
        "scenario_catalog_sha256": catalog_sha256,
        "generated_at": observed_now.isoformat(),
        "readiness": readiness.as_dict(),
        "evidence": {
            "case_evidence_record_ids": list(
                ledger.evidence_record_ids
            ),
            "case_outcome_record_ids": list(
                ledger.outcome_record_ids
            ),
            "outbound_message_ids": outbound_ids,
            "latest_material_at": (
                latest_material_at.isoformat()
                if latest_material_at is not None
                else None
            ),
            "observation_elapsed": observation_elapsed,
            "contains_payloads": False,
        },
    }
    receipt = {
        **receipt_without_hash,
        "receipt_sha256": _sha256(receipt_without_hash),
    }
    return ClosureSnapshot(
        scenario=scenario,
        readiness=readiness,
        receipt=receipt,
    )


def require_closure_ready(db: Session, ticket: Ticket) -> dict[str, Any]:
    """Compatibility name for the one governed close authorization."""

    from .notification_evidence_policy import (
        require_governed_closure_ready,
    )

    return require_governed_closure_ready(db, ticket)


def append_closure_receipt_event(
    db: Session,
    *,
    ticket_id: int,
    actor_id: int | None,
    receipt: dict[str, Any],
) -> TicketEvent:
    digest = str(receipt["receipt_sha256"])
    append_case_outcome(
        db,
        ticket_id=ticket_id,
        record_type="closure_assessment",
        state="closed",
        idempotency_key=f"closure-receipt:{digest}",
        occurred_at=utc_now(),
        created_by=actor_id,
        source_kind="closure_service",
        source_id=digest,
        payload={
            "receipt_sha256": digest,
            "scenario_key": receipt.get("scenario_key"),
            "closure_ready": bool(
                (receipt.get("readiness") or {}).get("closure_ready")
            ),
        },
    )
    row = TicketEvent(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=EventType.status_changed,
        field_name=CLOSURE_RECEIPT_FIELD,
        new_value=digest,
        note="Safe Effective Closure receipt persisted.",
        payload_json=_canonical_json(
            {
                "schema": "nexus.case-ledger-timeline-projection.v1",
                "receipt_sha256": digest,
                "contains_payloads": False,
            }
        ),
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
        db.query(CaseOutcomeRecord)
        .filter(
            CaseOutcomeRecord.ticket_id == ticket_id,
            CaseOutcomeRecord.record_type == "closure_assessment",
            CaseOutcomeRecord.state == "closed",
        )
        .order_by(CaseOutcomeRecord.sequence.desc())
        .first()
    )
    if prior is None:
        return None
    prior_digest = str(
        (prior.payload_json or {}).get("receipt_sha256")
        or prior.source_id
        or prior.id
    )
    reason_text = " ".join(str(reason or "").strip().split())[:500]
    reason_sha = hashlib.sha256(reason_text.encode("utf-8")).hexdigest()
    append_case_outcome(
        db,
        ticket_id=ticket_id,
        record_type="closure_assessment",
        state="reopened",
        idempotency_key=(
            f"closure-invalidation:{prior.id}:{reason_sha[:24]}"
        ),
        occurred_at=utc_now(),
        created_by=actor_id,
        parent_record_id=prior.id,
        source_kind="closure_service",
        source_id=prior_digest,
        payload={
            "invalidated_record_id": prior.id,
            "receipt_sha256": prior_digest,
            "reason_sha256": reason_sha,
            "reason_length": len(reason_text),
        },
    )
    row = TicketEvent(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=EventType.reopened,
        field_name=CLOSURE_RECEIPT_INVALIDATED_FIELD,
        old_value=prior_digest,
        new_value="invalidated",
        note=reason_text,
        payload_json=_canonical_json(
            {
                "schema": "nexus.case-ledger-timeline-projection.v1",
                "closure_outcome_record_id": prior.id,
                "receipt_sha256": prior_digest,
                "reason_sha256": reason_sha,
                "contains_payloads": False,
            }
        ),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _record_state(
    kind: str,
    key: str,
    state: str,
    allowed_waiver_reasons: set[str],
) -> tuple[str, str]:
    if kind == "action":
        return (
            "execution_attempt",
            "succeeded" if state in {"verified", "completed"} else state,
        )
    if kind == "outcome":
        return (
            "operational_outcome",
            "confirmed" if state in {"verified", "completed"} else state,
        )
    if state == "waived":
        if key not in allowed_waiver_reasons:
            raise ValueError("closure_notification_waiver_not_allowed")
        return "customer_notification", "waived"
    if key == "delivered" and state in {"verified", "completed"}:
        return "customer_notification", "delivered"
    if key == "sent" and state in {"verified", "completed"}:
        return "customer_notification", "accepted"
    return "customer_notification", state


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
) -> RecordedClosureEvidence:
    ensure_ticket_visible(current_user, ticket, db)
    ensure_can_change_status(
        current_user,
        ticket,
        TicketStatus.closed,
        db,
    )
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
    if (
        normalized_kind == "fact"
        and normalized_source not in _AUTHORITATIVE_FACT_SOURCE_KINDS
    ):
        raise ValueError("closure_fact_source_not_authoritative")
    if (
        normalized_kind == "outcome"
        and normalized_key == "business_result_confirmed"
        and normalized_source not in _BUSINESS_OUTCOME_SOURCE_KINDS
    ):
        raise ValueError(
            "closure_business_outcome_source_not_authoritative"
        )

    normalized_ref = str(source_ref or "").strip()
    normalized_revision = str(source_revision or "").strip()
    if not normalized_key or not normalized_ref or not normalized_revision:
        raise ValueError("closure_evidence_source_identity_required")

    scenario, _, _, error = _resolve_scenario(ticket)
    if scenario is None:
        raise ValueError(error or "scenario_unavailable")
    allowed_waiver_reasons = set(
        scenario.allowed_no_notification_reasons
    )
    allowed_keys = {
        "fact": set(scenario.required_fact_classes),
        "customer_input": set(scenario.required_customer_inputs),
        "action": set(scenario.allowed_action_classes),
        "outcome": set(scenario.required_outcome_levels),
        "notification": allowed_waiver_reasons | {"sent", "delivered"},
    }
    if normalized_key not in allowed_keys[normalized_kind]:
        raise ValueError(
            "closure_evidence_key_not_in_scenario_contract"
        )
    if normalized_kind != "notification" and normalized_state == "waived":
        raise ValueError("closure_evidence_waiver_kind_invalid")

    observed = ensure_utc(observed_at)
    if observed is None:
        raise ValueError("closure_evidence_observed_at_required")
    note_text = " ".join(str(note or "").strip().split())
    evidence_identity = {
        "schema": CLOSURE_EVIDENCE_SCHEMA,
        "ticket_id": ticket.id,
        "kind": normalized_kind,
        "key": normalized_key,
        "state": normalized_state,
        "source_kind": normalized_source,
        "source_ref": normalized_ref[:200],
        "source_revision": normalized_revision[:160],
        "observed_at": observed.isoformat(),
        "recorded_by": current_user.id,
    }
    digest = _sha256(evidence_identity)

    if normalized_kind in {"fact", "customer_input"}:
        row, created = record_case_evidence(
            db,
            ticket_id=ticket.id,
            evidence_kind=normalized_kind,
            evidence_key=normalized_key,
            state=normalized_state,
            source_kind=normalized_source,
            source_ref=normalized_ref,
            source_revision=normalized_revision,
            observed_at=observed,
            recorded_by=current_user.id,
            safe_metadata={
                "evidence_sha256": digest,
                "note_sha256": (
                    hashlib.sha256(note_text.encode("utf-8")).hexdigest()
                    if note_text
                    else None
                ),
                "note_length": len(note_text),
            },
        )
        return RecordedClosureEvidence(
            record_id=row.id,
            record_type="case_evidence",
            evidence_sha256=row.evidence_sha256,
            created=created,
        )

    record_type, outcome_state = _record_state(
        normalized_kind,
        normalized_key,
        normalized_state,
        allowed_waiver_reasons,
    )
    if normalized_kind == "action":
        payload: dict[str, Any] = {"action_class": normalized_key}
    elif normalized_kind == "outcome":
        payload = {"outcome_level": normalized_key}
    elif normalized_state == "waived":
        payload = {"waiver_reason": normalized_key}
    else:
        payload = {"notification_state": normalized_key}

    row, created = append_case_outcome(
        db,
        ticket_id=ticket.id,
        record_type=record_type,
        state=outcome_state,
        idempotency_key=f"closure-evidence:{digest}",
        occurred_at=observed,
        created_by=current_user.id,
        source_kind=normalized_source,
        source_id=normalized_ref,
        payload={
            **payload,
            "source_revision": normalized_revision[:160],
            "evidence_sha256": digest,
            "note_sha256": (
                hashlib.sha256(note_text.encode("utf-8")).hexdigest()
                if note_text
                else None
            ),
            "note_length": len(note_text),
        },
    )
    return RecordedClosureEvidence(
        record_id=row.id,
        record_type=record_type,
        evidence_sha256=digest,
        created=created,
    )
