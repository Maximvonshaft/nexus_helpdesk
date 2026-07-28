from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, attributes

from ..enums import EventType
from ..models import Ticket
from ..models_case_scenario import CaseScenarioAssignment
from ..utils.time import ensure_utc, utc_now
from .audit_service import log_event
from .nexus_osr.business_scenarios import (
    BusinessScenarioCatalog,
    BusinessScenarioDefinition,
    load_business_scenario_catalog,
)

CASE_SCENARIO_SNAPSHOT_SCHEMA = "nexus.case-scenario-assignment.v1"
SCENARIO_IDENTITY_FIELDS = (
    "case_type",
    "sub_category",
    "category",
    "ai_classification",
)


def _http_conflict(code: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, **details},
    )


def _utc(value: datetime | None = None) -> datetime:
    current = ensure_utc(value or utc_now())
    if current is None:
        raise RuntimeError("scenario_time_unavailable")
    return current


def load_runtime_scenario_catalog(
    *,
    at: datetime | None = None,
) -> BusinessScenarioCatalog:
    """Load the catalog without treating governance review as runtime expiry."""

    catalog = load_business_scenario_catalog(require_all_active=False)
    current = _utc(at)
    invalid = [
        item.scenario_key
        for item in catalog.scenarios
        if not scenario_is_runtime_active(item, at=current)
    ]
    if invalid:
        raise _http_conflict(
            "scenario_catalog_contains_runtime_inactive_definition",
            scenario_keys=sorted(invalid),
        )
    return catalog


def scenario_is_runtime_active(
    scenario: BusinessScenarioDefinition,
    *,
    at: datetime | None = None,
) -> bool:
    current = _utc(at)
    lifecycle = scenario.lifecycle
    return bool(
        lifecycle.status == "approved"
        and lifecycle.effective_from <= current
        and (lifecycle.expires_at is None or current < lifecycle.expires_at)
    )


def scenario_review_overdue(
    scenario: BusinessScenarioDefinition,
    *,
    at: datetime | None = None,
) -> bool:
    return _utc(at) >= scenario.lifecycle.review_due


def _normalized(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _candidate_matches(
    ticket: Ticket,
    catalog: BusinessScenarioCatalog,
) -> dict[str, str]:
    aliases = catalog.alias_map()
    matches: dict[str, str] = {}
    for field in SCENARIO_IDENTITY_FIELDS:
        value = _normalized(getattr(ticket, field, None))
        if value and value in aliases:
            matches[field] = aliases[value]
    return matches


def resolve_candidate_scenario(
    ticket: Ticket,
    catalog: BusinessScenarioCatalog,
    *,
    at: datetime | None = None,
) -> BusinessScenarioDefinition | None:
    matches = _candidate_matches(ticket, catalog)
    resolved = set(matches.values())
    if len(resolved) > 1:
        raise _http_conflict(
            "case_scenario_identity_conflict",
            ticket_id=getattr(ticket, "id", None),
            matches=matches,
        )
    if not resolved:
        return None
    scenario = catalog.by_key()[next(iter(resolved))]
    if not scenario_is_runtime_active(scenario, at=at):
        raise _http_conflict(
            "case_scenario_not_runtime_active",
            scenario_key=scenario.scenario_key,
        )
    return scenario


def resolve_explicit_scenario(
    catalog: BusinessScenarioCatalog,
    scenario_key: str,
    *,
    at: datetime | None = None,
) -> BusinessScenarioDefinition:
    normalized = _normalized(scenario_key)
    target = catalog.alias_map().get(normalized or "")
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "case_scenario_not_found"},
        )
    scenario = catalog.by_key()[target]
    if not scenario_is_runtime_active(scenario, at=at):
        raise _http_conflict(
            "case_scenario_not_runtime_active",
            scenario_key=scenario.scenario_key,
        )
    return scenario


def _scenario_snapshot(
    catalog: BusinessScenarioCatalog,
    scenario: BusinessScenarioDefinition,
) -> dict[str, Any]:
    lifecycle = scenario.lifecycle
    return {
        "schema": CASE_SCENARIO_SNAPSHOT_SCHEMA,
        "catalog_version": catalog.catalog_version,
        "catalog_sha256": catalog.source_sha256,
        "scenario": {
            "scenario_key": scenario.scenario_key,
            "issue_type_aliases": list(scenario.issue_type_aliases),
            "trigger_sources": list(scenario.trigger_sources),
            "required_fact_classes": list(scenario.required_fact_classes),
            "required_customer_inputs": list(scenario.required_customer_inputs),
            "risk_level": scenario.risk_level,
            "escalation_policy_key": scenario.escalation_policy_key,
            "owner_queue_key": scenario.owner_queue_key,
            "required_capabilities": list(scenario.required_capabilities),
            "allowed_action_classes": list(scenario.allowed_action_classes),
            "required_action_classes": list(scenario.required_action_classes),
            "blocked_action_classes": list(scenario.blocked_action_classes),
            "notification_policy": scenario.notification_policy,
            "allowed_no_notification_reasons": list(
                scenario.allowed_no_notification_reasons
            ),
            "terminal_behavior": scenario.terminal_behavior,
            "required_outcome_levels": list(scenario.required_outcome_levels),
            "completion_rules": list(scenario.completion_rules),
            "definition_of_done": scenario.definition_of_done,
            "observation_period_seconds": scenario.observation_period_seconds,
            "reopen_conditions": list(scenario.reopen_conditions),
            "cancellation_semantics": scenario.cancellation_semantics,
            "metrics": list(scenario.metrics),
            "scope_mode": scenario.scope_mode,
            "lifecycle": {
                "status": lifecycle.status,
                "owner": lifecycle.owner,
                "approved_at": lifecycle.approved_at.isoformat(),
                "effective_from": lifecycle.effective_from.isoformat(),
                "review_due": lifecycle.review_due.isoformat(),
                "expires_at": (
                    lifecycle.expires_at.isoformat()
                    if lifecycle.expires_at is not None
                    else None
                ),
                "supersedes": lifecycle.supersedes,
            },
        },
    }


def _assignment_values(
    ticket: Ticket,
    catalog: BusinessScenarioCatalog,
    scenario: BusinessScenarioDefinition,
    *,
    source: str,
    reason: str | None = None,
    actor_id: int | None = None,
    assigned_at: datetime | None = None,
) -> dict[str, Any]:
    snapshot = _scenario_snapshot(catalog, scenario)
    return {
        "ticket_id": int(ticket.id),
        "scenario_key": scenario.scenario_key,
        "catalog_version": catalog.catalog_version,
        "catalog_sha256": catalog.source_sha256,
        "scenario_snapshot_json": json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "assignment_source": str(source or "runtime")[:80],
        "assignment_reason": (reason or "")[:2000] or None,
        "assigned_by": actor_id,
        "assigned_at": _utc(assigned_at),
        "superseded_at": None,
        "superseded_by_id": None,
    }


def current_case_scenario_assignment(
    db: Session,
    *,
    ticket_id: int,
    lock: bool = False,
) -> CaseScenarioAssignment | None:
    query = db.query(CaseScenarioAssignment).filter(
        CaseScenarioAssignment.ticket_id == ticket_id,
        CaseScenarioAssignment.superseded_at.is_(None),
    )
    if lock and db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    return query.order_by(CaseScenarioAssignment.id.desc()).first()


def serialize_case_scenario_assignment(
    row: CaseScenarioAssignment,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    snapshot = json.loads(row.scenario_snapshot_json)
    review_due_raw = snapshot["scenario"]["lifecycle"]["review_due"]
    review_due = datetime.fromisoformat(str(review_due_raw).replace("Z", "+00:00"))
    if review_due.tzinfo is None:
        review_due = review_due.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "ticket_id": row.ticket_id,
        "scenario_key": row.scenario_key,
        "catalog_version": row.catalog_version,
        "catalog_sha256": row.catalog_sha256,
        "assignment_source": row.assignment_source,
        "assignment_reason": row.assignment_reason,
        "assigned_by": row.assigned_by,
        "assigned_at": row.assigned_at,
        "review_due": review_due,
        "review_overdue": _utc(at) >= review_due.astimezone(timezone.utc),
        "superseded_at": row.superseded_at,
        "superseded_by_id": row.superseded_by_id,
        "current": row.superseded_at is None,
    }


def reclassify_case_scenario(
    db: Session,
    *,
    ticket: Ticket,
    scenario_key: str,
    reason: str,
    actor_id: int | None,
) -> CaseScenarioAssignment:
    normalized_reason = " ".join(str(reason or "").split())
    if not normalized_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "case_scenario_reclassification_reason_required"},
        )
    catalog = load_runtime_scenario_catalog()
    scenario = resolve_explicit_scenario(catalog, scenario_key)
    current = current_case_scenario_assignment(
        db,
        ticket_id=ticket.id,
        lock=True,
    )
    if (
        current is not None
        and current.scenario_key == scenario.scenario_key
        and current.catalog_sha256 == catalog.source_sha256
    ):
        return current

    now = utc_now()
    old_summary = (
        {
            "assignment_id": current.id,
            "scenario_key": current.scenario_key,
            "catalog_version": current.catalog_version,
            "catalog_sha256": current.catalog_sha256,
        }
        if current is not None
        else None
    )
    if current is not None:
        current.superseded_at = now
        db.flush()

    row = CaseScenarioAssignment(
        **_assignment_values(
            ticket,
            catalog,
            scenario,
            source="explicit_reclassification",
            reason=normalized_reason,
            actor_id=actor_id,
            assigned_at=now,
        )
    )
    db.add(row)
    db.flush()
    if current is not None:
        current.superseded_by_id = row.id

    log_event(
        db,
        ticket_id=ticket.id,
        actor_id=actor_id,
        event_type=EventType.field_updated,
        field_name="scenario_assignment",
        old_value=current.scenario_key if current is not None else None,
        new_value=row.scenario_key,
        note=normalized_reason,
        payload={
            "old": old_summary,
            "new": {
                "assignment_id": row.id,
                "scenario_key": row.scenario_key,
                "catalog_version": row.catalog_version,
                "catalog_sha256": row.catalog_sha256,
            },
            "summary": "Case scenario explicitly reclassified",
        },
    )
    db.flush()
    attributes.set_committed_value(ticket, "case_type", row.scenario_key)
    return row


def _catalog_projection_key(row: CaseScenarioAssignment) -> str:
    """Project the canonical Assignment into legacy readers without restoring authority."""

    catalog = load_business_scenario_catalog(require_all_active=False)
    if (
        row.catalog_version != catalog.catalog_version
        or row.catalog_sha256 != catalog.source_sha256
    ):
        return "case_scenario_catalog_mismatch"
    return row.scenario_key


def project_case_scenario_to_legacy_identity(
    db: Session,
    ticket: Ticket,
) -> CaseScenarioAssignment | None:
    """Expose Assignment as a read-only compatibility projection on ``case_type``."""

    if ticket.id is None:
        return None
    row = current_case_scenario_assignment(db, ticket_id=int(ticket.id))
    if row is None:
        return None
    attributes.set_committed_value(ticket, "case_type", _catalog_projection_key(row))
    return row


def _connection_current_assignment(connection, ticket_id: int):
    table = CaseScenarioAssignment.__table__
    return connection.execute(
        select(table.c.id, table.c.scenario_key, table.c.catalog_sha256).where(
            table.c.ticket_id == ticket_id,
            table.c.superseded_at.is_(None),
        )
    ).mappings().first()


def _insert_automatic_assignment(
    connection,
    ticket: Ticket,
    *,
    source: str,
) -> None:
    if ticket.id is None or _connection_current_assignment(connection, ticket.id):
        return
    catalog = load_runtime_scenario_catalog()
    scenario = resolve_candidate_scenario(ticket, catalog)
    if scenario is None:
        return
    connection.execute(
        CaseScenarioAssignment.__table__.insert().values(
            **_assignment_values(
                ticket,
                catalog,
                scenario,
                source=source,
                actor_id=getattr(ticket, "created_by", None),
                assigned_at=getattr(ticket, "created_at", None) or utc_now(),
            )
        )
    )
    attributes.set_committed_value(ticket, "case_type", scenario.scenario_key)


def _identity_changed(ticket: Ticket) -> bool:
    state = inspect(ticket)
    return any(
        state.attrs[field].history.has_changes()
        for field in SCENARIO_IDENTITY_FIELDS
    )


@event.listens_for(Ticket, "after_insert")
def _assign_scenario_after_ticket_insert(
    mapper,
    connection,
    target: Ticket,
) -> None:  # noqa: ANN001
    del mapper
    _insert_automatic_assignment(connection, target, source="ticket_insert")


@event.listens_for(Ticket, "before_update")
def _guard_scenario_identity_update(
    mapper,
    connection,
    target: Ticket,
) -> None:  # noqa: ANN001
    del mapper
    if target.id is None or not _identity_changed(target):
        return

    catalog = load_runtime_scenario_catalog()
    matches = _candidate_matches(target, catalog)
    resolved = set(matches.values())
    current = _connection_current_assignment(connection, int(target.id))

    if current is not None:
        requested = sorted(key for key in resolved if key != current["scenario_key"])
        if requested:
            raise _http_conflict(
                "case_scenario_reclassification_command_required",
                ticket_id=target.id,
                current_scenario_key=current["scenario_key"],
                requested_scenario_keys=requested,
                matches=matches,
            )
        return

    if len(resolved) > 1:
        raise _http_conflict(
            "case_scenario_identity_conflict",
            ticket_id=target.id,
            matches=matches,
        )


@event.listens_for(Ticket, "after_update")
def _assign_scenario_after_ticket_update(
    mapper,
    connection,
    target: Ticket,
) -> None:  # noqa: ANN001
    del mapper
    _insert_automatic_assignment(connection, target, source="ticket_identity_update")
