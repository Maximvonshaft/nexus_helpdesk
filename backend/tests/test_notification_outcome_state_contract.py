from __future__ import annotations

from app.services.ticket_closure_readiness import _record_state


def test_sent_notification_evidence_maps_to_allowed_attempt_state():
    assert _record_state(
        "notification",
        "sent",
        "completed",
        set(),
    ) == ("customer_notification", "accepted")


def test_delivered_notification_evidence_maps_to_terminal_outcome():
    assert _record_state(
        "notification",
        "delivered",
        "completed",
        set(),
    ) == ("customer_notification", "delivered")
