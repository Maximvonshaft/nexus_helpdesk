from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult, safe_tracking_candidate
from app.services.webcall_ai.reply_builder import (
    build_handoff_reply,
    build_missing_tracking_reply,
    build_tracking_lookup_disabled_reply,
    build_tracking_reply,
)


def test_missing_tracking_reply_asks_for_tracking_number():
    assert build_missing_tracking_reply() == ""


def test_tracking_fact_ok_reply_uses_safe_fields_only():
    fact = TrackingFactResult(
        ok=True,
        tracking_number="SF123456789CN",
        status="in_transit",
        status_label="In transit",
        latest_event=TrackingFactEvent(
            event_time="2026-05-23T10:00:00Z",
            location="Zurich",
            description="Departed facility",
        ),
        checked_at="2026-05-23T11:00:00Z",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )

    reply = build_tracking_reply(fact)

    assert reply == ""
    assert "SF123456789CN" not in reply


def test_multiple_candidates_reply_uses_suffixes_only():
    fact = TrackingFactResult(
        ok=False,
        tool_status="multiple",
        failure_reason="multiple_waybill_candidates",
        safe_candidates=[
            safe_tracking_candidate("SF123456789CN", suffix="6789"),
            safe_tracking_candidate("SF987654321CN", suffix="4321"),
        ],
    )

    reply = build_tracking_reply(fact)

    assert reply == ""
    assert "SF123456789CN" not in reply
    assert "SF987654321CN" not in reply


def test_failure_and_handoff_replies_do_not_invent_status():
    failure = build_tracking_reply(TrackingFactResult(ok=False, tool_status="error", failure_reason="timeout"))

    assert failure == ""
    assert "delivered" not in failure.lower()
    assert build_handoff_reply() == ""
    assert build_tracking_lookup_disabled_reply() == ""
