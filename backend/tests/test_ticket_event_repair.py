from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.enums import EventType
from app.services.ticket_event_repair import (
    TicketEventRepairConflict,
    TicketEventRepairScopeError,
    apply_ticket_event_repairs,
    plan_ticket_event_repairs,
)
from app.services.ticket_event_writer import TICKET_EVENT_CONTRACT


def _row(
    row_id: int,
    *,
    ticket_id: int,
    event_type: EventType | str,
    payload: object,
    note: str | None = None,
) -> SimpleNamespace:
    payload_json = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    return SimpleNamespace(
        id=row_id,
        ticket_id=ticket_id,
        actor_id=None,
        event_type=event_type,
        created_at=None,
        field_name=None,
        old_value=None,
        new_value=None,
        note=note,
        payload_json=payload_json,
    )


def test_repair_plan_is_scope_bound_redacted_deterministic_and_idempotent() -> None:
    rows = [
        _row(
            3,
            ticket_id=1,
            event_type=EventType.outbound_sent,
            payload={
                "outbound_message_id": 30,
                "status": "sent",
                "customer_email": "person@example.com",
                "tracking_number": "CH020000129131",
            },
            note="Sent to person@example.com about CH020000129131",
        ),
        _row(
            4,
            ticket_id=99,
            event_type=EventType.outbound_sent,
            payload={"customer_phone": "+382 67 123 456"},
        ),
        _row(
            5,
            ticket_id=2,
            event_type=EventType.ticket_created,
            payload={
                "event_contract": TICKET_EVENT_CONTRACT,
                "event_class": "internal_audit",
                "schema_version": 1,
                "ticket_id": 2,
                "status": "created",
            },
        ),
    ]

    plan = plan_ticket_event_repairs(
        rows,
        tenant_id="tenant-a",
        authorized_ticket_ids={1, 2},
    )

    assert plan.tenant_id == "tenant-a"
    assert plan.scanned_count == 2
    assert plan.changed_count == 1
    assert [decision.event_id for decision in plan.decisions] == [3]
    summary = plan.summary_json()
    assert "person@example.com" not in summary
    assert "CH020000129131" not in summary
    assert "+382 67 123 456" not in summary
    assert "replacement_payload" not in summary
    assert json.loads(summary)["changed_count"] == 1

    applied = apply_ticket_event_repairs(rows, plan)
    assert applied.changed_count == 1
    repaired = next(row for row in rows if row.id == 3)
    assert TICKET_EVENT_CONTRACT in repaired.payload_json
    assert "person@example.com" not in repaired.payload_json
    assert "CH020000129131" not in repaired.payload_json
    assert "person@example.com" not in (repaired.note or "")
    assert "CH020000129131" not in (repaired.note or "")

    second_plan = plan_ticket_event_repairs(
        rows,
        tenant_id="tenant-a",
        authorized_ticket_ids={1, 2},
    )
    assert second_plan.changed_count == 0
    assert second_plan.decisions == ()


@pytest.mark.parametrize(
    ("tenant_id", "authorized_ticket_ids"),
    [
        ("", {1}),
        ("default", {1}),
        ("tenant-a", set()),
        ("tenant-a", None),
    ],
)
def test_repair_rejects_missing_default_or_empty_scope_before_payload_access(
    tenant_id: str,
    authorized_ticket_ids: set[int] | None,
) -> None:
    class ExplosiveRow:
        id = 1
        ticket_id = 1
        event_type = EventType.ticket_created

        @property
        def payload_json(self):
            raise AssertionError("payload must not be read before scope validation")

    with pytest.raises(TicketEventRepairScopeError):
        plan_ticket_event_repairs(
            [ExplosiveRow()],
            tenant_id=tenant_id,
            authorized_ticket_ids=authorized_ticket_ids,
        )


def test_repair_does_not_mutate_unclassified_event_type() -> None:
    row = _row(
        8,
        ticket_id=1,
        event_type="future_event",
        payload={"customer_email": "person@example.com"},
    )

    plan = plan_ticket_event_repairs(
        [row],
        tenant_id="tenant-a",
        authorized_ticket_ids={1},
    )

    assert plan.scanned_count == 1
    assert plan.changed_count == 0
    assert plan.unclassified_count == 1
    assert plan.decisions == ()
    assert "person@example.com" in row.payload_json


def test_apply_detects_concurrent_payload_change_by_digest() -> None:
    row = _row(
        11,
        ticket_id=1,
        event_type=EventType.outbound_sent,
        payload={"customer_email": "person@example.com"},
    )
    plan = plan_ticket_event_repairs(
        [row],
        tenant_id="tenant-a",
        authorized_ticket_ids={1},
    )
    assert plan.changed_count == 1

    row.payload_json = json.dumps({"status": "changed-concurrently"})

    with pytest.raises(TicketEventRepairConflict):
        apply_ticket_event_repairs([row], plan)


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("ticket_id", 2),
        ("actor_id", 7),
        ("event_type", EventType.ticket_created),
    ],
)
def test_apply_detects_concurrent_identity_change_by_digest(
    attribute: str,
    value: object,
) -> None:
    row = _row(
        12,
        ticket_id=1,
        event_type=EventType.outbound_sent,
        payload={"customer_email": "person@example.com"},
    )
    plan = plan_ticket_event_repairs(
        [row],
        tenant_id="tenant-a",
        authorized_ticket_ids={1},
    )
    assert plan.changed_count == 1

    setattr(row, attribute, value)

    with pytest.raises(TicketEventRepairConflict):
        apply_ticket_event_repairs([row], plan)


def test_invalid_json_is_repaired_to_bounded_marker_without_echoing_input() -> None:
    raw = '{"customer_email":"person@example.com"'
    row = _row(
        13,
        ticket_id=1,
        event_type=EventType.internal_note_added,
        payload=raw,
    )

    plan = plan_ticket_event_repairs(
        [row],
        tenant_id="tenant-a",
        authorized_ticket_ids={1},
    )
    assert plan.changed_count == 1
    apply_ticket_event_repairs([row], plan)

    payload = json.loads(row.payload_json)
    assert payload["event_contract"] == TICKET_EVENT_CONTRACT
    assert payload["event_class"] == "internal_audit"
    assert payload["redacted"] is True
    assert payload["category"] == "historical_payload_invalid_json"
    assert "person@example.com" not in row.payload_json


def test_cli_scope_resolver_uses_server_owned_ticket_associations_and_rejects_ambiguity() -> (
    None
):
    from scripts.repair_ticket_events import resolve_authorized_ticket_ids

    class FakeQuery:
        def __init__(self, *, rows=(), first=None) -> None:
            self.rows = list(rows)
            self.first_value = first
            self.filters: list[object] = []
            self.locked = False

        def filter(self, *conditions: object):
            self.filters.extend(conditions)
            return self

        def with_for_update(self):
            self.locked = True
            return self

        def all(self):
            return list(self.rows)

        def first(self):
            return self.first_value

    class FakeSession:
        def __init__(self, queries: list[FakeQuery]) -> None:
            self.queries = list(queries)

        def query(self, *_columns: object):
            assert self.queries, "unexpected resolver query"
            return self.queries.pop(0)

    db = FakeSession(
        [
            FakeQuery(rows=[(11,), (12,)]),
            FakeQuery(rows=[(12,), (13,)]),
            FakeQuery(first=None),
            FakeQuery(first=None),
        ]
    )
    assert resolve_authorized_ticket_ids(db, "tenant-a") == frozenset({11, 12, 13})

    locking_queries = [
        FakeQuery(rows=[(11,), (12,)]),
        FakeQuery(rows=[(12,), (13,)]),
        FakeQuery(first=None),
        FakeQuery(first=None),
    ]
    locking_db = FakeSession(locking_queries)
    assert resolve_authorized_ticket_ids(
        locking_db,
        "tenant-a",
        lock_for_update=True,
    ) == frozenset({11, 12, 13})
    assert all(query.locked for query in locking_queries)

    ambiguous = FakeSession(
        [
            FakeQuery(rows=[(11,)]),
            FakeQuery(rows=[]),
            FakeQuery(first=(1,)),
            FakeQuery(first=None),
        ]
    )
    with pytest.raises(TicketEventRepairScopeError):
        resolve_authorized_ticket_ids(ambiguous, "tenant-a")


def test_cli_apply_batch_requests_database_row_lock() -> None:
    from scripts.repair_ticket_events import load_ticket_event_batch

    class FakeQuery:
        def __init__(self) -> None:
            self.locked = False

        def filter(self, *_conditions: object):
            return self

        def order_by(self, *_columns: object):
            return self

        def limit(self, _limit: int):
            return self

        def with_for_update(self):
            self.locked = True
            return self

        def all(self):
            return []

    class FakeSession:
        def __init__(self) -> None:
            self.query_object = FakeQuery()

        def query(self, *_models: object):
            return self.query_object

    db = FakeSession()
    assert load_ticket_event_batch(
        db,
        ticket_ids=frozenset({1}),
        after_id=0,
        limit=10,
        lock_for_update=True,
    ) == []
    assert db.query_object.locked is True


def test_cli_scope_resolver_rejects_default_without_querying() -> None:
    from scripts.repair_ticket_events import resolve_authorized_ticket_ids

    class ExplosiveSession:
        def query(self, *_columns: object):
            raise AssertionError("default Tenant must fail before database access")

    with pytest.raises(TicketEventRepairScopeError):
        resolve_authorized_ticket_ids(ExplosiveSession(), "default")
