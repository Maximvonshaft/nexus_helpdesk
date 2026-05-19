from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_speedaf_enqueue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api import webchat_fast  # noqa: E402
from app.services.webchat_fast_session_service import FastBusinessState  # noqa: E402


def _state(*, issue_type: str = "delivery_reschedule", tracking_number: str | None = "SPX123456789CH") -> FastBusinessState:
    return FastBusinessState(
        intent=issue_type,
        issue_type=issue_type,
        tracking_number=tracking_number,
        fast_issue_key=f"tracking:{tracking_number}:intent:{issue_type}" if tracking_number else f"session:s1:intent:{issue_type}",
        missing_fields=(),
    )


def test_caller_id_is_extracted_from_visitor_phone():
    visitor = webchat_fast.WebchatFastVisitor(phone="  +41 79 000 0000  ")

    assert webchat_fast._caller_id(visitor) == "+41 79 000 0000"
    assert webchat_fast._caller_id(None) is None


def test_delivery_follow_up_detection_is_conservative():
    assert webchat_fast._is_delivery_follow_up_request(
        body="Please urge delivery for this package, it is still not delivered.",
        business_state=_state(issue_type="tracking_lookup"),
    ) is True
    assert webchat_fast._is_delivery_follow_up_request(
        body="What are your customer service hours?",
        business_state=_state(issue_type="general_question", tracking_number=None),
    ) is False
    assert webchat_fast._is_delivery_follow_up_request(
        body="Please deliver again tomorrow.",
        business_state=_state(issue_type="delivery_reschedule"),
    ) is True


def test_speedaf_work_order_enqueue_requires_ticket_waybill_caller_and_delivery_intent(monkeypatch):
    calls = []

    def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=987)

    monkeypatch.setattr(webchat_fast, "enqueue_speedaf_work_order_create_job", fake_enqueue)

    visitor = webchat_fast.WebchatFastVisitor(phone="41000000000")
    job_id = webchat_fast._maybe_enqueue_speedaf_work_order(
        db=object(),
        ticket_id=123,
        conversation_id=456,
        business_state=_state(issue_type="tracking_lookup", tracking_number="SPX123456789CH"),
        body="Please urge delivery for my parcel.",
        visitor=visitor,
        handoff_reason="delivery follow up required",
        recommended_action="delivery follow-up",
    )

    assert job_id == 987
    assert calls == [
        {
            "db": calls[0]["db"],
            "ticket_id": 123,
            "conversation_id": 456,
            "waybill_code": "SPX123456789CH",
            "caller_id": "41000000000",
            "description": "WebChat delivery follow-up request: Please urge delivery for my parcel.",
            "work_order_type": "WT0103-05",
        }
    ]


def test_speedaf_work_order_enqueue_skips_without_required_data(monkeypatch):
    calls = []
    monkeypatch.setattr(webchat_fast, "enqueue_speedaf_work_order_create_job", lambda **kwargs: calls.append(kwargs))

    assert webchat_fast._maybe_enqueue_speedaf_work_order(
        db=object(),
        ticket_id=123,
        conversation_id=456,
        business_state=_state(tracking_number=None),
        body="Please urge delivery.",
        visitor=webchat_fast.WebchatFastVisitor(phone="41000000000"),
    ) is None
    assert webchat_fast._maybe_enqueue_speedaf_work_order(
        db=object(),
        ticket_id=123,
        conversation_id=456,
        business_state=_state(tracking_number="SPX123456789CH"),
        body="Please urge delivery.",
        visitor=None,
    ) is None
    assert webchat_fast._maybe_enqueue_speedaf_work_order(
        db=object(),
        ticket_id=123,
        conversation_id=456,
        business_state=_state(issue_type="general_question", tracking_number="SPX123456789CH"),
        body="What are your working hours?",
        visitor=webchat_fast.WebchatFastVisitor(phone="41000000000"),
    ) is None
    assert calls == []
