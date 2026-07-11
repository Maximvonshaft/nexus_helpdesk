from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.enums import EventType, SourceChannel
from app.models import Ticket
from app.services import customer_visible_message_service as service
from app.services.customer_visible_message_service import CustomerVisibleMessageResult
from app.services.ticket_event_sanitizer import (
    MAX_TICKET_EVENT_BYTES,
    sanitize_ticket_event_payload,
    serialize_ticket_event_payload,
)


class FakeSession:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self._next_id = 100

    def add(self, row: object) -> None:
        if getattr(row, "id", None) is None:
            try:
                setattr(row, "id", self._next_id)
                self._next_id += 1
            except Exception:
                pass
        self.rows.append(row)

    def flush(self) -> None:
        return None


def test_ticket_event_sanitizer_redacts_nested_sensitive_payload_and_keeps_safe_ids() -> None:
    raw = {
        "message_id": 17,
        "ticket_id": 42,
        "customer_email": "person@example.com",
        "customer_phone": "+382 67 123 456",
        "tracking_number": "CH020000129131",
        "provider_payload": {
            "authorization": "Bearer " + ("A" * 30),
            "group_id": "123456789012345@g.us",
        },
        "tool_arguments": {
            "address": "12 Main Street",
            "phone": "+382 67 123 456",
        },
    }

    payload = sanitize_ticket_event_payload(raw)
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["message_id"] == 17
    assert payload["ticket_id"] == 42
    for forbidden in (
        "person@example.com",
        "+382 67 123 456",
        "CH020000129131",
        "Bearer ",
        "123456789012345@g.us",
        "12 Main Street",
    ):
        assert forbidden not in encoded
    assert "redacted" in encoded.lower()


def test_ticket_event_sanitizer_fails_closed_for_cycle_and_oversize() -> None:
    cycle: dict[str, object] = {"message_id": 9}
    cycle["self"] = cycle
    cycle_payload = sanitize_ticket_event_payload(cycle)
    cycle_encoded = json.dumps(cycle_payload, sort_keys=True)
    assert cycle_payload["message_id"] == 9
    assert "cycle" in cycle_encoded or "redacted" in cycle_encoded

    oversized = {
        "message_id": 10,
        **{f"safe_{index}": "x" * 240 for index in range(64)},
    }
    oversized_payload = sanitize_ticket_event_payload(oversized)
    oversized_encoded = serialize_ticket_event_payload(oversized)
    assert oversized_payload["message_id"] == 10
    assert oversized_payload["redacted"] is True
    assert oversized_payload["category"] == "event_payload_too_large"
    assert len(oversized_encoded.encode("utf-8")) <= MAX_TICKET_EVENT_BYTES


def test_unsupported_or_non_mapping_payload_is_bounded() -> None:
    payload = sanitize_ticket_event_payload(object())
    assert payload["redacted"] is True
    assert payload["present"] is True
    assert payload["category"] in {"unsupported_object", "event_payload_invalid"}
    assert len(serialize_ticket_event_payload(object()).encode("utf-8")) <= MAX_TICKET_EVENT_BYTES


def test_customer_visible_message_persists_only_sanitized_ticket_event(monkeypatch: pytest.MonkeyPatch) -> None:
    outbound = SimpleNamespace(id=88)

    def fake_outbound(*_args, **_kwargs):
        return CustomerVisibleMessageResult(
            outbound_message=outbound,
            customer_visible=True,
            provider_status="sent",
        )

    monkeypatch.setattr(service, "create_customer_visible_outbound", fake_outbound)
    db = FakeSession()
    ticket = Ticket(id=42, ticket_no="T-42", title="Visible reply")

    result = service.create_customer_visible_message(
        db,
        ticket=ticket,
        channel=SourceChannel.web_chat,
        body="Safe customer reply",
        origin="agent_manual",
        created_by=7,
        provider_status="sent",
        create_external_comment=False,
        event_type=EventType.outbound_sent,
        event_note="customer visible send",
        event_payload={
            "message_id": 17,
            "customer_email": "person@example.com",
            "tracking_number": "CH020000129131",
            "provider_payload": {"authorization": "Bearer " + ("A" * 30)},
            "tool_arguments": {"address": "12 Main Street"},
        },
    )

    assert result.ticket_event is not None
    payload = json.loads(result.ticket_event.payload_json)
    assert payload["ticket_id"] == 42
    assert payload["message_id"] == 17
    assert payload["outbound_message_id"] == 88
    assert result.ticket_event.actor_id == 7
    encoded = result.ticket_event.payload_json
    for forbidden in (
        "person@example.com",
        "CH020000129131",
        "Bearer ",
        "12 Main Street",
    ):
        assert forbidden not in encoded
    assert result.ticket_event in db.rows
