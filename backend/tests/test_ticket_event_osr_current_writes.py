from __future__ import annotations

from types import SimpleNamespace

from app.services.ticket_event_classification import resolve_ticket_event_class
from app.services.ticket_event_sanitizer import TICKET_EVENT_CONTRACT
from app.services.ticket_event_writer import TicketEventClass
from app.services import webchat_osr_audit_service
from app.services.nexus_osr import escalation_orchestration_service


def _governed(captured: dict[str, object]) -> dict[str, object]:
    event_class = captured["event_class"]
    assert isinstance(event_class, TicketEventClass)
    return {
        **dict(captured["payload"]),
        "event_contract": TICKET_EVENT_CONTRACT,
        "event_class": event_class.value,
        "schema_version": 1,
    }


def _assert_repair_classifies_current_write(captured: dict[str, object]) -> None:
    assert resolve_ticket_event_class(
        captured["event_type"],
        field_name=captured.get("field_name"),
        payload=_governed(captured),
        note=captured.get("note"),
    ) is captured["event_class"]


def test_webchat_osr_audit_current_write_is_provider_repair_classifiable(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def capture(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(webchat_osr_audit_service.TicketEventWriter, "add", capture)
    monkeypatch.setattr(webchat_osr_audit_service, "safe_write_webchat_event", lambda *args, **kwargs: None)

    webchat_osr_audit_service._record_osr_ticket_event(
        SimpleNamespace(),
        ticket=SimpleNamespace(id=11),
        conversation=SimpleNamespace(id=12),
        turn=SimpleNamespace(id=13),
        visitor_message=SimpleNamespace(id=14),
        summary={"mode": "audit_only", "audit_id": 15},
    )

    assert captured["event_class"] is TicketEventClass.PROVIDER
    _assert_repair_classifies_current_write(captured)


def test_osr_escalation_without_handoff_is_internal_repair_classifiable(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def capture(_db, **kwargs):
        captured.update(kwargs)

    class FakeSession:
        def flush(self) -> None:
            return None

    monkeypatch.setattr(escalation_orchestration_service.TicketEventWriter, "add", capture)
    monkeypatch.setattr(escalation_orchestration_service, "safe_write_webchat_event", lambda *args, **kwargs: None)

    escalation_orchestration_service._write_orchestration_events(
        FakeSession(),
        ticket=SimpleNamespace(id=21),
        conversation=SimpleNamespace(id=22),
        payload={
            "source": "nexus_osr",
            "action": "create_ticket",
            "human_status": "unavailable",
            "audit_id": 23,
            "ticket_created": True,
        },
    )

    assert captured["event_class"] is TicketEventClass.INTERNAL_AUDIT
    assert captured["field_name"] == "ticket.escalation_orchestration"
    _assert_repair_classifies_current_write(captured)
