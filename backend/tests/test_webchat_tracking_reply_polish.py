from __future__ import annotations

from app.services.webchat_ai_decision_runtime.service import _sanitize_reply_for_trusted_tracking


TRACKING_METADATA = {
    "fact_evidence_present": True,
    "pii_redacted": True,
    "tracking_number_hash": "sha256:test",
}


def test_polishes_duplicate_tracking_number_ending_phrase() -> None:
    reply = "Your tracking number tracking number ending 005451 is currently shown as out for delivery."

    polished = _sanitize_reply_for_trusted_tracking(
        reply,
        tracking_number="CH120000005451",
        tracking_fact_metadata=TRACKING_METADATA,
    )

    assert polished == "Your parcel ending 005451 is currently shown as out for delivery."
    assert "tracking number tracking number" not in polished.lower()


def test_replaces_raw_waybill_with_suffix_only_phrase() -> None:
    reply = "Your tracking number CH120000005451 is currently out for delivery."

    polished = _sanitize_reply_for_trusted_tracking(
        reply,
        tracking_number="CH120000005451",
        tracking_fact_metadata=TRACKING_METADATA,
    )

    assert polished == "Your parcel ending 005451 is currently out for delivery."
    assert "CH120000005451" not in polished


def test_polishes_tracking_number_parcel_ending_intermediate_phrase() -> None:
    reply = "Your tracking number parcel ending 005451 is currently out for delivery."

    polished = _sanitize_reply_for_trusted_tracking(
        reply,
        tracking_number="CH120000005451",
        tracking_fact_metadata=TRACKING_METADATA,
    )

    assert polished == "Your parcel ending 005451 is currently out for delivery."


def test_does_not_polish_without_trusted_tracking_fact() -> None:
    reply = "Your tracking number CH120000005451 is currently out for delivery."

    unchanged = _sanitize_reply_for_trusted_tracking(
        reply,
        tracking_number="CH120000005451",
        tracking_fact_metadata={"fact_evidence_present": False, "pii_redacted": True},
    )

    assert unchanged == reply
