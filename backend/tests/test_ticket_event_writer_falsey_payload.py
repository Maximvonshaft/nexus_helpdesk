from __future__ import annotations

import json

import pytest

from app.enums import EventType
from app.services import audit_service
from app.services.ticket_event_writer import (
    TICKET_EVENT_CONTRACT,
    TicketEventClass,
    TicketEventWriter,
)


class _FakeSession:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.flush_count = 0

    def add(self, row: object) -> None:
        self.rows.append(row)

    def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.parametrize("payload", [[], "", 0, False])
def test_falsey_non_mapping_payloads_persist_bounded_invalid_evidence(
    payload: object,
) -> None:
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload=payload,
    )

    persisted = json.loads(row.payload_json or "{}")
    assert persisted["event_contract"] == TICKET_EVENT_CONTRACT
    assert persisted["event_class"] == TicketEventClass.INTERNAL_AUDIT.value
    assert persisted["schema_version"] == 1
    assert persisted["redacted"] is True
    assert persisted["present"] is True
    assert persisted["category"] == "event_payload_invalid"
    assert isinstance(persisted.get("sha256_prefix"), str)
    assert len(persisted["sha256_prefix"]) == 16


@pytest.mark.parametrize("payload", [[], "", 0, False])
def test_audit_facade_preserves_falsey_non_mapping_for_writer(
    payload: object,
) -> None:
    db = _FakeSession()

    row = audit_service.log_event(
        db,
        ticket_id=42,
        actor_id=None,
        event_type=EventType.internal_note_added,
        payload=payload,
    )

    persisted = json.loads(row.payload_json or "{}")
    assert db.rows == [row]
    assert db.flush_count == 1
    assert persisted["event_contract"] == TICKET_EVENT_CONTRACT
    assert persisted["event_class"] == TicketEventClass.INTERNAL_AUDIT.value
    assert persisted["schema_version"] == 1
    assert persisted["redacted"] is True
    assert persisted["present"] is True
    assert persisted["category"] == "event_payload_invalid"
    assert isinstance(persisted.get("sha256_prefix"), str)
    assert len(persisted["sha256_prefix"]) == 16
    assert repr(payload) not in (row.payload_json or "")


@pytest.mark.parametrize("payload", [None, {}])
def test_none_and_empty_mapping_remain_valid_empty_payloads(payload: object) -> None:
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload=payload,
    )

    assert json.loads(row.payload_json or "{}") == {
        "event_contract": TICKET_EVENT_CONTRACT,
        "event_class": TicketEventClass.INTERNAL_AUDIT.value,
        "schema_version": 1,
    }


@pytest.mark.parametrize("payload", [None, {}])
def test_audit_facade_only_defaults_none_to_valid_empty_mapping(
    payload: object,
) -> None:
    db = _FakeSession()

    row = audit_service.log_event(
        db,
        ticket_id=42,
        actor_id=None,
        event_type=EventType.internal_note_added,
        payload=payload,
    )

    assert json.loads(row.payload_json or "{}") == {
        "event_contract": TICKET_EVENT_CONTRACT,
        "event_class": TicketEventClass.INTERNAL_AUDIT.value,
        "schema_version": 1,
    }
