from __future__ import annotations

from sqlalchemy import event, insert
from sqlalchemy.engine import Connection

from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..models_scenario_assignment import TicketScenarioAssignment
from ..utils.time import utc_now
from .scenario_assignment_service import ASSIGNMENT_EVENT_SCHEMA
from .scenario_contract import (
    canonical_json,
    current_scenario_catalog,
    freeze_scenario,
    legacy_alias_matches,
)

_INSTALLED = False


def _after_ticket_insert(
    _mapper,
    connection: Connection,
    target: Ticket,
) -> None:
    catalog = current_scenario_catalog()
    matched, observed = legacy_alias_matches(
        values=(
            ("case_type", target.case_type),
            ("sub_category", target.sub_category),
            ("category", target.category),
            ("ai_classification", target.ai_classification),
        ),
        catalog=catalog,
    )
    if not matched:
        return
    if len(matched) != 1:
        raise RuntimeError(
            "ticket_scenario_assignment_conflict:"
            f"ticket={target.id}:matches={','.join(sorted(matched))}:"
            f"observed={'|'.join(observed)}"
        )
    scenario = catalog.by_key()[next(iter(matched))]
    frozen = freeze_scenario(catalog, scenario)
    now = utc_now()
    connection.execute(
        insert(TicketScenarioAssignment).values(
            ticket_id=target.id,
            tenant_id=target.tenant_id,
            scenario_key=frozen.scenario.scenario_key,
            assignment_revision=1,
            catalog_version=frozen.catalog_version,
            catalog_sha256=frozen.catalog_sha256,
            definition_sha256=frozen.definition_sha256,
            definition_json=frozen.definition_json,
            assignment_source="ticket_create_projection",
            assignment_reason="resolved aliases: " + ", ".join(observed),
            assigned_by=target.created_by,
            assigned_at=now,
            updated_at=now,
        )
    )
    connection.execute(
        insert(TicketEvent).values(
            ticket_id=target.id,
            actor_id=target.created_by,
            event_type=EventType.field_updated,
            field_name="scenario_assignment",
            old_value=None,
            new_value=frozen.scenario.scenario_key,
            note="Scenario assigned from unambiguous creation aliases.",
            payload_json=canonical_json(
                {
                    "schema": ASSIGNMENT_EVENT_SCHEMA,
                    "scenario_key": frozen.scenario.scenario_key,
                    "assignment_revision": 1,
                    "catalog_version": frozen.catalog_version,
                    "catalog_sha256": frozen.catalog_sha256,
                    "definition_sha256": frozen.definition_sha256,
                    "assignment_source": "ticket_create_projection",
                    "observed_aliases": observed,
                    "contains_payloads": False,
                }
            ),
            created_at=now,
        )
    )


def install_ticket_scenario_assignment_events() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Ticket, "after_insert", _after_ticket_insert)
    _INSTALLED = True


__all__ = ["install_ticket_scenario_assignment_events"]
