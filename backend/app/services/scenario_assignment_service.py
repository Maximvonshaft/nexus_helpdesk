from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import EventType, TicketStatus
from ..models import Ticket, TicketEvent
from ..models_case_governance import TicketSLAAssignment, TicketSLAPauseInterval
from ..models_scenario_assignment import TicketScenarioAssignment
from ..models_sla_runtime import TicketSLATarget
from ..utils.time import ensure_utc, utc_now
from .nexus_osr.business_scenarios import (
    CATALOG_SCHEMA,
    BusinessScenarioCatalogError,
    BusinessScenarioDefinition,
    parse_business_scenario_catalog,
)
from .scenario_contract import (
    ScenarioContractError,
    canonical_json,
    current_scenario_catalog,
    freeze_scenario,
    legacy_alias_matches,
    resolve_catalog_scenario,
    scenario_is_operationally_active,
    sha256_json,
)
from .tenant_authority import tenant_runtime_authority_mode

ASSIGNMENT_EVENT_SCHEMA = "nexus.ticket-scenario-assignment.v1"
_RISK_PRIORITY = {"critical": 10, "high": 25, "medium": 50, "low": 70}


class TicketScenarioAssignmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScenarioExecutionPolicy:
    ticket_id: int
    scenario_key: str
    assignment_revision: int
    catalog_version: str
    catalog_sha256: str
    definition_sha256: str
    owner_queue_key: str
    risk_level: str
    required_capabilities: frozenset[str]
    allowed_actions: frozenset[str]
    blocked_actions: frozenset[str]
    escalation_policy_key: str | None
    lifecycle_review_due: datetime
    review_overdue: bool
    expires_at: datetime | None

    def agent_is_eligible(self, capabilities: Iterable[str]) -> bool:
        normalized = {str(value).strip().lower() for value in capabilities}
        return self.required_capabilities.issubset(normalized)

    def action_is_allowed(self, action_class: str) -> bool:
        action = str(action_class or "").strip().lower()
        return bool(
            action
            and action in self.allowed_actions
            and action not in self.blocked_actions
        )


@dataclass(frozen=True)
class AssignedScenario:
    assignment: TicketScenarioAssignment
    scenario: BusinessScenarioDefinition
    policy: ScenarioExecutionPolicy


def _scope_matches(ticket: Ticket, assignment: TicketScenarioAssignment) -> bool:
    return ticket.tenant_id == assignment.tenant_id


def _snapshot_scenario(
    assignment: TicketScenarioAssignment,
) -> BusinessScenarioDefinition:
    definition = assignment.definition_json
    if not isinstance(definition, dict):
        raise TicketScenarioAssignmentError("scenario_definition_snapshot_invalid")
    if sha256_json(definition) != assignment.definition_sha256:
        raise TicketScenarioAssignmentError("scenario_definition_digest_mismatch")
    lifecycle = definition.get("lifecycle") or {}
    approved_at = lifecycle.get("approved_at")
    if not approved_at:
        raise TicketScenarioAssignmentError("scenario_definition_lifecycle_invalid")
    root = {
        "schema": CATALOG_SCHEMA,
        "catalog_version": assignment.catalog_version,
        "owner": str(lifecycle.get("owner") or "frozen-assignment"),
        "approved_at": approved_at,
        "scope_mode": definition.get("scope_mode") or "inherit_resolved_scope",
        "scenarios": [definition],
    }
    try:
        catalog = parse_business_scenario_catalog(
            root,
            source_sha256=assignment.catalog_sha256,
        )
    except BusinessScenarioCatalogError as exc:
        raise TicketScenarioAssignmentError(exc.reason) from exc
    scenario = catalog.by_key().get(assignment.scenario_key)
    if scenario is None:
        raise TicketScenarioAssignmentError("scenario_definition_key_mismatch")
    if not scenario_is_operationally_active(scenario):
        raise TicketScenarioAssignmentError("scenario_not_operationally_active")
    return scenario


def _execution_policy(
    assignment: TicketScenarioAssignment,
    scenario: BusinessScenarioDefinition,
    *,
    at: datetime | None = None,
) -> ScenarioExecutionPolicy:
    observed = ensure_utc(at or utc_now())
    review_due = ensure_utc(scenario.lifecycle.review_due)
    if observed is None:
        raise TicketScenarioAssignmentError("scenario_policy_time_unavailable")
    if review_due is None:
        raise TicketScenarioAssignmentError("scenario_review_due_invalid")
    return ScenarioExecutionPolicy(
        ticket_id=assignment.ticket_id,
        scenario_key=scenario.scenario_key,
        assignment_revision=assignment.assignment_revision,
        catalog_version=assignment.catalog_version,
        catalog_sha256=assignment.catalog_sha256,
        definition_sha256=assignment.definition_sha256,
        owner_queue_key=scenario.owner_queue_key,
        risk_level=scenario.risk_level,
        required_capabilities=frozenset(scenario.required_capabilities),
        allowed_actions=frozenset(scenario.allowed_action_classes),
        blocked_actions=frozenset(scenario.blocked_action_classes),
        escalation_policy_key=scenario.escalation_policy_key,
        lifecycle_review_due=review_due,
        review_overdue=observed >= review_due,
        expires_at=ensure_utc(scenario.lifecycle.expires_at),
    )


def get_assigned_scenario(
    db: Session,
    *,
    ticket: Ticket,
    required: bool = True,
) -> AssignedScenario | None:
    assignment = db.get(TicketScenarioAssignment, ticket.id)
    if assignment is None:
        if required:
            raise TicketScenarioAssignmentError("scenario_assignment_missing")
        return None
    if not _scope_matches(ticket, assignment):
        raise TicketScenarioAssignmentError("scenario_assignment_tenant_conflict")
    if assignment.tenant_id is None and tenant_runtime_authority_mode() != "shadow":
        raise TicketScenarioAssignmentError("scenario_assignment_shadow_forbidden")
    scenario = _snapshot_scenario(assignment)
    return AssignedScenario(
        assignment=assignment,
        scenario=scenario,
        policy=_execution_policy(assignment, scenario),
    )


def _ticket_alias_values(ticket: Ticket):
    return (
        ("case_type", ticket.case_type),
        ("sub_category", ticket.sub_category),
        ("category", ticket.category),
        ("ai_classification", ticket.ai_classification),
    )


def backfill_ticket_scenario_assignment(
    db: Session,
    *,
    ticket: Ticket,
    actor_id: int | None = None,
    reason: str = "explicit legacy backfill",
) -> TicketScenarioAssignment:
    existing = db.get(TicketScenarioAssignment, ticket.id)
    if existing is not None:
        return existing
    catalog = current_scenario_catalog()
    matched, observed = legacy_alias_matches(
        values=_ticket_alias_values(ticket),
        catalog=catalog,
    )
    if not matched:
        raise TicketScenarioAssignmentError("scenario_identity_missing")
    if len(matched) != 1:
        raise TicketScenarioAssignmentError("scenario_identity_conflict")
    return assign_ticket_scenario(
        db,
        ticket=ticket,
        scenario_key=next(iter(matched)),
        actor_id=actor_id,
        source="explicit_legacy_backfill",
        reason=f"{reason}; aliases={','.join(observed)}",
        allow_reclassification=False,
    )


def _invalidate_sla_for_reclassification(
    db: Session,
    *,
    ticket: Ticket,
    actor_id: int | None,
    now: datetime,
) -> dict[str, Any]:
    """Retire only SLA projections derived from the superseded Scenario.

    Pause history remains append-only. An open pause is explicitly closed, then
    the new SLA is calculated from the original Case creation time plus the full
    governed pause history; service elapsed time is never reset by reclassification.
    """

    target = (
        db.query(TicketSLATarget)
        .filter(TicketSLATarget.ticket_id == ticket.id)
        .first()
    )
    assignment = (
        db.query(TicketSLAAssignment)
        .filter(TicketSLAAssignment.ticket_id == ticket.id)
        .first()
    )
    open_pause = (
        db.query(TicketSLAPauseInterval)
        .filter(
            TicketSLAPauseInterval.ticket_id == ticket.id,
            TicketSLAPauseInterval.ended_at.is_(None),
        )
        .first()
    )
    if open_pause is not None:
        open_pause.ended_at = now
        open_pause.ended_by = actor_id
    old = {
        "sla_assignment_id": assignment.id if assignment is not None else None,
        "sla_policy_revision_id": (
            assignment.policy_revision_id if assignment is not None else None
        ),
        "sla_target_id": target.id if target is not None else None,
        "open_pause_closed": open_pause is not None,
    }
    if target is not None:
        db.delete(target)
    if assignment is not None:
        db.delete(assignment)
    ticket.sla_policy_id = None
    ticket.first_response_due_at = None
    ticket.resolution_due_at = None
    ticket.sla_paused = False
    ticket.sla_paused_at = None
    ticket.sla_pause_reason = None
    ticket.first_response_breached = False
    ticket.resolution_breached = False
    db.flush()
    return old


def _rebuild_sla_after_reclassification(
    db: Session,
    *,
    ticket: Ticket,
    actor_id: int | None,
) -> dict[str, Any]:
    from .sla_service import (
        SLAConfigurationError,
        apply_policy_to_ticket,
        get_policy_for_priority,
    )

    policy = get_policy_for_priority(db, ticket.priority)
    if policy is None:
        return {"status": "unavailable", "reason": "sla_policy_missing"}
    try:
        apply_policy_to_ticket(
            ticket,
            policy,
            db=db,
            assigned_by=actor_id,
        )
    except SLAConfigurationError as exc:
        if str(exc) == "approved_sla_revision_missing":
            return {"status": "unavailable", "reason": str(exc)}
        raise
    return {
        "status": "assigned",
        "sla_policy_id": ticket.sla_policy_id,
        "first_response_due_at": (
            ticket.first_response_due_at.isoformat()
            if ticket.first_response_due_at is not None
            else None
        ),
        "resolution_due_at": (
            ticket.resolution_due_at.isoformat()
            if ticket.resolution_due_at is not None
            else None
        ),
    }


def assign_ticket_scenario(
    db: Session,
    *,
    ticket: Ticket,
    scenario_key: str,
    actor_id: int | None,
    source: str,
    reason: str | None,
    allow_reclassification: bool,
) -> TicketScenarioAssignment:
    catalog = current_scenario_catalog()
    try:
        scenario = resolve_catalog_scenario(catalog, scenario_key)
    except ScenarioContractError as exc:
        raise TicketScenarioAssignmentError(str(exc)) from exc
    frozen = freeze_scenario(catalog, scenario)
    now = utc_now()
    existing = db.get(TicketScenarioAssignment, ticket.id)
    old_payload: dict[str, Any] | None = None
    sla_transition: dict[str, Any] | None = None
    if existing is None:
        assignment = TicketScenarioAssignment(
            ticket_id=ticket.id,
            tenant_id=ticket.tenant_id,
            scenario_key=frozen.scenario.scenario_key,
            assignment_revision=1,
            catalog_version=frozen.catalog_version,
            catalog_sha256=frozen.catalog_sha256,
            definition_sha256=frozen.definition_sha256,
            definition_json=frozen.definition_json,
            assignment_source=str(source or "operator")[:40],
            assignment_reason=str(reason).strip() if reason else None,
            assigned_by=actor_id,
            assigned_at=now,
            updated_at=now,
        )
        db.add(assignment)
    else:
        assignment = existing
        if not _scope_matches(ticket, assignment):
            raise TicketScenarioAssignmentError("scenario_assignment_tenant_conflict")
        if assignment.scenario_key == frozen.scenario.scenario_key:
            return assignment
        if not allow_reclassification:
            raise TicketScenarioAssignmentError("scenario_reclassification_required")
        old_payload = {
            "scenario_key": assignment.scenario_key,
            "assignment_revision": assignment.assignment_revision,
            "catalog_version": assignment.catalog_version,
            "catalog_sha256": assignment.catalog_sha256,
            "definition_sha256": assignment.definition_sha256,
        }
        sla_retired = _invalidate_sla_for_reclassification(
            db,
            ticket=ticket,
            actor_id=actor_id,
            now=now,
        )
        assignment.assignment_revision += 1
        assignment.scenario_key = frozen.scenario.scenario_key
        assignment.catalog_version = frozen.catalog_version
        assignment.catalog_sha256 = frozen.catalog_sha256
        assignment.definition_sha256 = frozen.definition_sha256
        assignment.definition_json = frozen.definition_json
        assignment.assignment_source = str(source or "operator")[:40]
        assignment.assignment_reason = str(reason).strip() if reason else None
        assignment.assigned_by = actor_id
        assignment.assigned_at = now
        assignment.updated_at = now
        ticket.assignee_id = None
        if ticket.status not in {TicketStatus.closed, TicketStatus.canceled}:
            ticket.status = TicketStatus.pending_assignment
        ticket.resolution_summary = None
        ticket.updated_at = now
        db.flush()
        sla_transition = {
            "retired": sla_retired,
            "replacement": _rebuild_sla_after_reclassification(
                db,
                ticket=ticket,
                actor_id=actor_id,
            ),
        }

    db.flush()
    event_payload = {
        "schema": ASSIGNMENT_EVENT_SCHEMA,
        "scenario_key": assignment.scenario_key,
        "assignment_revision": assignment.assignment_revision,
        "catalog_version": assignment.catalog_version,
        "catalog_sha256": assignment.catalog_sha256,
        "definition_sha256": assignment.definition_sha256,
        "assignment_source": assignment.assignment_source,
        "assignment_reason": assignment.assignment_reason,
        "old_assignment": old_payload,
        "sla_transition": sla_transition,
        "contains_payloads": False,
    }
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=actor_id,
            event_type=EventType.field_updated,
            field_name="scenario_assignment",
            old_value=(old_payload or {}).get("scenario_key") if old_payload else None,
            new_value=assignment.scenario_key,
            note=assignment.assignment_reason,
            payload_json=canonical_json(event_payload),
            created_at=now,
        )
    )
    db.flush()
    return assignment


def require_scenario_action_allowed(
    db: Session,
    *,
    ticket: Ticket,
    action_class: str,
) -> ScenarioExecutionPolicy:
    assigned = get_assigned_scenario(db, ticket=ticket, required=True)
    assert assigned is not None
    if not assigned.policy.action_is_allowed(action_class):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="scenario_action_not_allowed",
        )
    return assigned.policy


def scenario_routing_priority(
    ticket: Ticket,
    policy: ScenarioExecutionPolicy,
    *,
    now: datetime | None = None,
) -> int:
    observed = ensure_utc(now or utc_now())
    created = ensure_utc(ticket.created_at)
    priority = _RISK_PRIORITY.get(policy.risk_level, 60)
    if ticket.status == TicketStatus.escalated:
        priority -= 15
    due_values = [
        ensure_utc(value)
        for value in (ticket.first_response_due_at, ticket.resolution_due_at)
        if value is not None
    ]
    due_values = [value for value in due_values if value is not None]
    if observed is not None and due_values:
        remaining = min((value - observed).total_seconds() for value in due_values)
        if remaining <= 0:
            priority = 0
        elif remaining <= 1800:
            priority -= 15
        elif remaining <= 7200:
            priority -= 7
    if observed is not None and created is not None:
        waiting_minutes = max(0, int((observed - created).total_seconds() // 60))
        priority -= min(20, waiting_minutes // 15)
    return max(0, min(100, int(priority)))


__all__ = [
    "AssignedScenario",
    "ScenarioExecutionPolicy",
    "TicketScenarioAssignmentError",
    "assign_ticket_scenario",
    "backfill_ticket_scenario_assignment",
    "get_assigned_scenario",
    "require_scenario_action_allowed",
    "scenario_is_operationally_active",
    "scenario_routing_priority",
]
