from __future__ import annotations

import json

import pytest
from app.enums import EventType
from app.services.ticket_event_sanitizer import MAX_TICKET_EVENT_BYTES
from app.services.ticket_event_writer import (
    TICKET_EVENT_CONTRACT,
    TicketEventClass,
    TicketEventWriteError,
    TicketEventWriter,
    policy_for_event_class,
)


class FakeSession:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.flush_count = 0
        self.commit_count = 0

    def add(self, row: object) -> None:
        self.rows.append(row)

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        self.commit_count += 1


@pytest.mark.parametrize(
    ("event_class", "safe_id_key", "safe_id_value"),
    [
        (TicketEventClass.CUSTOMER_VISIBLE, "outbound_message_id", 11),
        (TicketEventClass.TRACKING, "audit_id", "tracking-audit-12"),
        (TicketEventClass.TOOL, "tool_call_log_id", 13),
        (TicketEventClass.PROVIDER, "audit_id", "provider-audit-14"),
        (TicketEventClass.DISPATCH, "dispatch_outbox_id", 15),
        (TicketEventClass.INTERNAL_AUDIT, "handoff_request_id", 16),
    ],
)
def test_writer_injects_versioned_metadata_for_every_event_class(
    event_class: TicketEventClass,
    safe_id_key: str,
    safe_id_value: int | str,
) -> None:
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=event_class,
        payload={
            safe_id_key: safe_id_value,
            "event_contract": "caller-controlled",
            "event_class": "caller-controlled",
            "schema_version": 999,
            "status": "executed",
        },
    )

    payload = json.loads(row.payload_json or "{}")
    assert payload["event_contract"] == TICKET_EVENT_CONTRACT
    assert payload["event_class"] == event_class.value
    assert payload["schema_version"] == 1
    assert payload[safe_id_key] == safe_id_value
    assert payload["status"] == "executed"


def test_internal_audit_preserves_webcall_note_join_identifiers() -> None:
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload={
            # Intentionally high entropy: the generic audit label sanitizer
            # classifies some such public IDs as credential-like.
            "voice_session_id": "wv_Kh8yeLZOq0z9dOwCNJ4vJVT",
            "note_id": 17,
            "customer_phone": "+382 67 123 456",
        },
    )

    payload = json.loads(row.payload_json or "{}")
    assert payload["voice_session_id"] == "wv_Kh8yeLZOq0z9dOwCNJ4vJVT"
    assert payload["note_id"] == 17
    assert "+382 67 123 456" not in (row.payload_json or "")


def test_internal_audit_drops_invalid_voice_session_operational_reference() -> None:
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload={
  "voice_session_id": "person@example.com",
  "note_id": 18,
  "status": "saved",
        },
    )

    payload = json.loads(row.payload_json or "{}")
    assert payload["note_id"] == 18
    assert payload["status"] == "saved"
    assert "voice_session_id" not in payload
    assert "person@example.com" not in (row.payload_json or "")

def test_customer_visible_preserves_only_bounded_operational_message_references() -> None:
    thread_id = "<nexusdesk-ticket-42@example.test>"
    message_id = "<customer-reply@example.test>"
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.outbound_queued,
        event_class=TicketEventClass.CUSTOMER_VISIBLE,
        payload={
            "outbound_message_id": 18,
            "mailbox_thread_id": thread_id,
            "mailbox_message_id": message_id,
            "mailbox_references": f"{thread_id} {message_id}",
            "provider_message_id": "smtp-provider-18",
            "customer_email": "person@example.com",
        },
    )

    payload = json.loads(row.payload_json or "{}")
    assert payload["mailbox_thread_id"] == thread_id
    assert payload["mailbox_message_id"] == message_id
    assert payload["mailbox_references"] == f"{thread_id} {message_id}"
    assert payload["provider_message_id"] == "smtp-provider-18"
    assert "person@example.com" not in (row.payload_json or "")


def test_invalid_operational_references_are_dropped_without_widening_generic_policy() -> None:
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.outbound_queued,
        event_class=TicketEventClass.CUSTOMER_VISIBLE,
        payload={
            "mailbox_thread_id": "person@example.com",
            "mailbox_message_id": "<bad id@example.test>",
            "mailbox_references": "<one@example.test> invalid",
            "provider_message_id": "provider id with spaces",
            "status": "queued",
        },
    )
    payload = json.loads(row.payload_json or "{}")
    assert payload["status"] == "queued"
    assert "mailbox_thread_id" not in payload
    assert "mailbox_message_id" not in payload
    assert "mailbox_references" not in payload
    assert "provider_message_id" not in payload


def test_tool_policy_drops_cross_class_identifiers_and_redacts_arguments_and_results() -> (
    None
):
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.internal_note_added,
        event_class=TicketEventClass.TOOL,
        payload={
            "tool_call_log_id": 9,
            "dispatch_outbox_id": 100,
            "tool_name": "tracking.lookup",
            "status": "executed",
            "arguments": {
                "tracking_number": "CH020000129131",
                "address": "12 Main Street",
            },
            "tool_result": {
                "customer_email": "person@example.com",
                "customer_phone": "+382 67 123 456",
            },
        },
    )

    payload = json.loads(row.payload_json or "{}")
    encoded = row.payload_json or ""
    assert payload["tool_call_log_id"] == 9
    assert "dispatch_outbox_id" not in payload
    assert payload["tool_name"] == "tracking.lookup"
    assert payload["status"] == "executed"
    for forbidden in (
        "CH020000129131",
        "12 Main Street",
        "person@example.com",
        "+382 67 123 456",
    ):
        assert forbidden not in encoded


def test_writer_fails_closed_for_cycle_oversize_unicode_and_unsupported_objects() -> (
    None
):
    cycle: dict[str, object] = {"ticket_id": 42}
    cycle["self"] = cycle

    cycle_row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload=cycle,
    )
    cycle_payload = json.loads(cycle_row.payload_json or "{}")
    assert cycle_payload["event_contract"] == TICKET_EVENT_CONTRACT
    assert "CH020000129131" not in (cycle_row.payload_json or "")
    assert len((cycle_row.payload_json or "").encode("utf-8")) <= MAX_TICKET_EVENT_BYTES

    oversized_row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload={f"field_{index}": "数据🚚" * 500 for index in range(64)},
    )
    oversized_payload = json.loads(oversized_row.payload_json or "{}")
    assert oversized_payload["redacted"] is True
    assert oversized_payload["category"] == "event_payload_too_large"
    assert oversized_payload["event_contract"] == TICKET_EVENT_CONTRACT
    assert (
        len((oversized_row.payload_json or "").encode("utf-8"))
        <= MAX_TICKET_EVENT_BYTES
    )

    unsupported_row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload={"unsupported": object()},
    )
    assert "unsupported_object" in (unsupported_row.payload_json or "")


def test_writer_redacts_and_bounds_orm_text_fields() -> None:
    bearer_value = "Bear" + "er " + ("a" * 32)
    row = TicketEventWriter.build(
        ticket_id=42,
        actor_id=7,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        field_name="status",
        old_value="person@example.com " + ("x" * 900),
        new_value=bearer_value,
        note="Call +382 67 123 456 about CH020000129131 at 12 Main Street "
        + ("z" * 1500),
        payload={},
    )

    assert row.field_name == "status"
    assert row.actor_id == 7
    assert "person@example.com" not in (row.old_value or "")
    assert bearer_value not in (row.new_value or "")
    assert "+382 67 123 456" not in (row.note or "")
    assert "CH020000129131" not in (row.note or "")
    assert "12 Main Street" not in (row.note or "")
    assert len(row.old_value or "") <= 500
    assert len(row.new_value or "") <= 500
    assert len(row.note or "") <= 1000


def test_add_preserves_transaction_ownership() -> None:
    db = FakeSession()

    row = TicketEventWriter.add(
        db,
        ticket_id=42,
        event_type=EventType.ticket_created,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload={"ticket_id": 42, "status": "created"},
    )

    assert db.rows == [row]
    assert db.flush_count == 1
    assert db.commit_count == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "ticket_id": 0,
            "event_type": EventType.ticket_created,
            "event_class": TicketEventClass.INTERNAL_AUDIT,
        },
        {
            "ticket_id": True,
            "event_type": EventType.ticket_created,
            "event_class": TicketEventClass.INTERNAL_AUDIT,
        },
        {
            "ticket_id": 1,
            "actor_id": 0,
            "event_type": EventType.ticket_created,
            "event_class": TicketEventClass.INTERNAL_AUDIT,
        },
        {
            "ticket_id": 1,
            "event_type": "ticket_created",
            "event_class": TicketEventClass.INTERNAL_AUDIT,
        },
        {
            "ticket_id": 1,
            "event_type": EventType.ticket_created,
            "event_class": "internal_audit",
        },
        {
            "ticket_id": 1,
            "event_type": EventType.ticket_created,
            "event_class": object(),
        },
    ],
)
def test_writer_rejects_invalid_identity_type_and_class(
    kwargs: dict[str, object]
) -> None:
    with pytest.raises(TicketEventWriteError):
        TicketEventWriter.build(**kwargs)


def test_policy_registry_is_complete_and_immutable() -> None:
    policies = [policy_for_event_class(event_class) for event_class in TicketEventClass]
    assert {policy.event_class for policy in policies} == {
        event_class.value for event_class in TicketEventClass
    }
    assert all(policy.contract == TICKET_EVENT_CONTRACT for policy in policies)
    assert all(policy.schema_version == 1 for policy in policies)
    with pytest.raises((AttributeError, TypeError)):
        policies[0].schema_version = 2  # type: ignore[misc]


def test_structured_payloads_are_class_scoped_and_recursively_redacted() -> None:
    dispatch_row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.DISPATCH,
        payload={
            "event": "operations_dispatch_routing",
            "routing": {
                "status": "routed",
                "outbox_id": 77,
                "dispatch_status": "pending",
                "group_hash": "group-hash-1",
                "chat_jid": "123456789012345@g.us",
                "authorization": "Bear" + "er " + ("A" * 32),
            },
        },
    )
    dispatch_payload = json.loads(dispatch_row.payload_json or "{}")
    assert dispatch_payload["routing"]["outbox_id"] == 77
    assert dispatch_payload["routing"]["dispatch_status"] == "pending"
    encoded = dispatch_row.payload_json or ""
    assert "123456789012345@g.us" not in encoded
    assert "Bear" + "er " not in encoded

    internal_row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        payload={
            "operator_projection": "human_review_required",
            "case_context_state": {
                "has_tracking_reference": True,
                "has_contact_method": True,
                "missing_info_count": 2,
                "customer_email": "person@example.com",
            },
        },
    )
    internal_payload = json.loads(internal_row.payload_json or "{}")
    assert internal_payload["case_context_state"]["has_tracking_reference"] is True
    assert internal_payload["case_context_state"]["has_contact_method"] is True
    assert "person@example.com" not in (internal_row.payload_json or "")

    tool_row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.field_updated,
        event_class=TicketEventClass.TOOL,
        payload={"routing": {"outbox_id": 77}},
    )
    tool_payload = json.loads(tool_row.payload_json or "{}")
    assert "routing" not in tool_payload
    assert tool_payload["redacted"] is True
